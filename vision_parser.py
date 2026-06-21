#!/usr/bin/env python3
"""
Vision Report Parser
将 LLM 输出的 markdown 分析结果解析为结构化数据
"""

import re
import json
from datetime import datetime
from pathlib import Path
from typing import Optional
from entity_resolver import detect_currency


# ============ 风险/机会关键词库 ============
RISK_KEYWORDS = [
    # 通用风险
    "downside", "risk", "risks", "下调", "卖出", "减持", "低于预期", "miss",
    "weakness", "weak", "decline", "fall", "pressure", "headwind", "headwinds",
    "concern", "worry", "uncertainty", "uncertain", "volatile", "volatility",
    "oversupply", "overcapacity", "excess supply", "inventory correction",
    "price erosion", "margin compression", "competition intensifies",
    "geopolitical risk", "export control", "sanction", "blacklist",

    # 行业特定风险
    "demand weakness", "utilization rate decline", "price cut", "ASP decline",
    "capacity glut", "technology obsolescence", "technology transition risk",
    "customer concentration", "supply chain disruption"
]

OPPORTUNITY_KEYWORDS = [
    # 通用机会
    "upside", "opportunity", "买入", "增持", "超预期", "beat", "outperform",
    "upgrade", "上调", "strong", "strength", "growth", "increase", "rise",
    "bullish", "positive", "recovery", "rebound", "turnaround",
    "target price raised", "raising target", "multiple upgrades",

    # 行业特定机会
    "AI driven", "AI demand", "HBM shortage", "capacity tight",
    "new product launch", "design win", "market share gain",
    "margin expansion", "cost reduction", "operational efficiency",
    "pricing power", "strong order book", "backlog growth"
]


def extract_company(markdown: str) -> Optional[str]:
    """提取公司名称"""
    # Section headers that should never be matched as company names
    section_headers = {
        "business", "risk", "opportunity", "investment", "revenue",
        "key findings", "report summary", "rating", "conclusion",
        "in this report", "this report", "note", "summary",
        "disclaimer", "disclosure", "important", "analyst",
        "median multiples", "peer comparison", "valuation",
    }
    # Phrases that are definitely NOT company names
    garbage_names = {
        "in this report", "this report", "median multiples",
        "please see", "refer to", "source:", "note:",
    }

    patterns = [
        # Pattern 0: "**公司：** GlobalFoundries Inc (格芯)" — colon inside bold
        r'\*?\*?公司[：:]\*?\*?\s+([一-鿿A-Za-z][一-鿿A-Za-z\s&.（）()]{2,48}?)\s*$',
        # Pattern 1: "**公司**：高通公司 (Qualcomm Incorporated)" or "公司: English Inc."
        r'\*?\*?公司\*?\*?[：:\s]+\*?\*?(?:[一-鿿]+\s*[（(]\s*([A-Za-z][A-Za-z\s&.]+?)\s*[）)])',
        # Pattern 2: "Company: Name"
        r'\*?\*?Company\*?\*?[:\s]+\*?\*?([A-Za-z][A-Za-z\s&.]+?)\*?\*?\s*$',
        # Pattern 3: "Company Name: Name"
        r'Company\s+Name[:\s]+([A-Za-z][A-Za-z\s&.]+)',
        # Pattern 4: "股票代码：QCOM US" + "公司：高通公司" → fallback to Chinese
        r'\*?\*?公司\*?\*?[：:\s]+\*?\*?([一-鿿A-Za-z][一-鿿A-Za-z\s&.（）()]{2,48}?)\*?\*?\s*$',
        # Pattern 5: "Name (TICKER)" at start of report
        r'^([A-Z][A-Za-z\s&.]+?)\s*\([A-Z0-9.]+\)',
        # Pattern 6: Ticker-based - "TICKER - Company Name"
        r'Ticker[:\s]*[A-Z0-9.]+\s*[-–—]\s*([A-Z][A-Za-z\s&.]+?)(?:\n|$)',
    ]
    for p in patterns:
        m = re.search(p, markdown, re.IGNORECASE | re.MULTILINE)
        if m:
            name = m.group(1).strip().rstrip('.*')
            if not name:
                continue
            # Filter out section headers
            if name.lower() in section_headers or name.lower() in garbage_names:
                continue
            if len(name) < 2 or len(name) > 60:
                continue
            return name

    # Fallback: product code → company mapping
    PRODUCT_COMPANY_MAP = {
        "GB200": "NVIDIA", "GB300": "NVIDIA", "NVL72": "NVIDIA",
        "Rubin": "NVIDIA", "Blackwell": "NVIDIA", "H100": "NVIDIA", "H200": "NVIDIA", "B100": "NVIDIA", "B200": "NVIDIA",
        "MI300": "AMD", "MI400": "AMD",
        "Gaudi": "Intel",
        "Trainium": "Amazon", "Inferentia": "Amazon",
        "TPU v": "Google",
    }
    for code, company in PRODUCT_COMPANY_MAP.items():
        if code.lower() in markdown.lower():
            return company
    return None


def extract_ticker(markdown: str) -> Optional[str]:
    """提取股票代码"""
    # Pattern 0: "**股票代码**：QCOM US" (Chinese format, space-separated)
    m = re.search(r'股票代码\*?\*?[：:\s]+([A-Z0-9]{2,6})\s*(?:US|TW|TT|HK|SS|SZ)?', markdown, re.IGNORECASE)
    if m:
        ticker = m.group(1).strip()
        suffix = re.search(r'(?:US|TW|TT|HK|SS|SZ)', markdown[m.start():m.end()], re.IGNORECASE)
        return f"{ticker}.{suffix.group()}" if suffix else ticker

    # Pattern 0.5: "Ticker: QCOM US" (English format, space-separated)
    m = re.search(r'Ticker\*?\*?[:\s]+([A-Z0-9]{2,6})\s+(US|TW|TT|HK|SS|SZ)\b', markdown, re.IGNORECASE)
    if m:
        return f"{m.group(1)}.{m.group(2)}"

    # Pattern 1: 优先匹配标准交易所格式 XXXX.TW, 2454 TT, 2454TW (有点或无点)
    std_pattern = r'\b(\d{4,5})\s*[.\s]\s*(?:TW|TT|US|HK|SS|SZ)\b'
    m = re.search(std_pattern, markdown, re.IGNORECASE)
    if m:
        suffix = re.search(r'(?:TW|TT|US|HK|SS|SZ)', markdown[m.start():m.end()], re.IGNORECASE)
        return f"{m.group(1)}.{suffix.group()}" if suffix else m.group(1)

    # Pattern 2: 字母代码.交易所 (如 NVDA.US)
    letter_pattern = r'\b([A-Z]{2,5})\.(?:US|HK|TW|TT|SS|SZ)\b'
    m = re.search(letter_pattern, markdown)
    if m:
        return m.group(0).strip()

    # Pattern 3: "**Ticker:** 2454.TW" or "Ticker: 2454.TW"
    m = re.search(r'\*?\*?Ticker\*?\*?[:\s]+([A-Z0-9.]+)', markdown, re.IGNORECASE)
    if m:
        ticker = m.group(1).strip().rstrip('.*')
        return ticker

    # Pattern 4: "Stock Code: 2454"
    m = re.search(r'Stock\s*Code[:\s]+([A-Z0-9.]+)', markdown, re.IGNORECASE)
    if m:
        return m.group(1).strip().rstrip('.*')

    return None


def extract_rating(markdown: str) -> Optional[str]:
    """提取评级"""
    patterns = [
        r'\*?\*?Rating\*?\*?[:\s]+(Buy|Neutral|Sell|Outperform|Underperform|Overweight|Underweight|增持|中性|减持)',
        r'Recommendation[:\s]+(Buy|Neutral|Sell|Outperform|Overweight)',
        r'Investment\s*Rating[:\s]+(Buy|Neutral|Sell|Outperform)',
        r'Action[:\s]+(Buy|Neutral|Sell|Upgrade|Downgrade)',
    ]
    for p in patterns:
        m = re.search(p, markdown, re.IGNORECASE)
        if m:
            rating = m.group(1).strip()
            # 标准化：大小写 + 同义词统一
            rating_lower = rating.lower()
            if rating_lower in ['outperform', 'overweight', '买入', '增持', 'strong buy', 'top pick']:
                return 'Buy'
            elif rating_lower in ['neutral', 'underweight', '中性', 'equal-weight', 'market perform', 'hold']:
                return 'Neutral'
            elif rating_lower in ['underperform', 'sell', '减持', '卖出', 'reduce']:
                return 'Sell'
            elif rating_lower == 'buy':
                return 'Buy'
            elif rating_lower == 'neutral':
                return 'Neutral'
            elif rating_lower == 'sell':
                return 'Sell'
            return rating.title()
    return None


def extract_target_price(markdown: str) -> dict:
    """提取目标价"""
    result = {'new': None, 'old': None, 'currency': 'USD'}

    # Strip markdown bold/italic to simplify regex
    markdown = re.sub(r'\*\*([^*]+)\*\*', r'\1', markdown)
    markdown = re.sub(r'\*([^*]+)\*', r'\1', markdown)

    # Skip if report explicitly says no TP — only check near TP lines, not entire report
    tp_search_area = '\n'.join(
        line for line in markdown[:3000].split('\n')
        if re.search(r'目标价|Target\s*Price|Price\s*Target|PT\b', line, re.IGNORECASE)
    )
    if tp_search_area:
        # Only skip if the report says there's NO new TP (not just missing old TP)
        no_new_tp_patterns = [
            r'(?:not\s+applicable|no\s+target\s+price|不适用|N/A)',
            r'(?:does\s+not\s+provide.*?(?:target|rating))',
        ]
        for pat in no_new_tp_patterns:
            m = re.search(pat, tp_search_area, re.IGNORECASE)
            if m:
                # Don't block if it's only the OLD TP that's missing
                context = tp_search_area[max(0, m.start()-30):m.end()+30]
                if not re.search(r'(?:旧|此前|previous|prior|old)', context, re.IGNORECASE):
                    return None

    # Normalize numbers: fullwidth commas, English commas, spaces
    def _normalize_number(s: str) -> str:
        return s.replace('，', '').replace(',', '').replace('。', '.').replace('、', '').replace(' ', '')

    currency_prefix = r'(?:NT\$|TWD|US\$|USD|HKD|HK\$|CNY|RMB|Rmb|W|₩|KRW|EUR|€|JPY|¥|\$)?'
    patterns_new = [
        # Chinese: "**目标价**：3,100,000 韩元 (此前：1,700,000)"
        r'目标价\**(?:（[^）]*）)?[：:\s]+' + currency_prefix + r'\s*([\d，。、,.]{3,30})',
        # Chinese: "**新目标价**：1，130 美元" / "新目标价（12个月）：A股137元"
        r'新目标价\**(?:（[^）]*）)?[：:\s]+' + currency_prefix + r'\s*([\d，。、,.]+)',
        # Chinese: "新目标Rmb710.00" / "新目标RMB123"
        r'新目标' + r'(?:价\**)?' + r'(?:' + currency_prefix + r')?' + r'\s*[：:\s]*' + currency_prefix + r'\s*([\d，。、,.]{2,20})',
        # "PT ↑" or "PT Rmb710"
        r'\bPT\s*(?:↑|：|:|\s)+' + currency_prefix + r'\s*([\d，。、,.\s]{2,20})',
        # English: "Target Price: 1,130 USD"
        r'Target Price[:\s]+' + currency_prefix + r'\s*([\d，。、,.\s]+?)(?:\s*(?:USD|TWD|HKD|CNY|KRW|美元|港元|人民币)?(?:\s|$))',
        r'Target[:\s]+' + currency_prefix + r'\s*([\d，。、,.\s]+?)(?:\s*(?:USD|TWD|HKD|CNY|KRW|美元)?(?:\s|$))',
        r'Price Target[:\s]+' + currency_prefix + r'\s*([\d，。、,.\s]+?)(?:\s*(?:USD|TWD|HKD|CNY|KRW|美元)?(?:\s|$))',
    ]
    for p in patterns_new:
        m = re.search(p, markdown, re.IGNORECASE)
        if m:
            try:
                val = _normalize_number(m.group(1))
                result['new'] = float(val)
                break
            except ValueError:
                continue

    # 旧目标价 - 支持多种货币前缀
    patterns_old = [
        # Chinese: "此前：1,700,000 韩元" or "(此前：1,700,000)"
        r'[（(]?\s*此前[：:\s]+' + currency_prefix + r'\s*([\d，。、, ]{3,30})',
        # Chinese: "**旧目标价**：950 美元"
        r'旧目标价\**[：:\s]+' + currency_prefix + r'\s*([\d，。、 ]+)',
        # "(Previous: NT$2,160)" or "Previous: 2,160 TWD" or "(Previous: W285,000)"
        r'\(?\s*Previous[:\s]+' + currency_prefix + r'\s*([\d，。、,.\s]+?)(?:\s*(?:USD|TWD|HKD|CNY|KRW|美元)?(?:\s|[)])|$)',
        # "Old Target: NT$1,988"
        r'Old\s*Target[:\s]+' + currency_prefix + r'\s*([\d，。、,.\s]+?)(?:\s*(?:USD|TWD|HKD|CNY|KRW|美元)?(?:\s|$))',
        # "Prior Target: 1,280"
        r'Prior\s*Target[:\s]+' + currency_prefix + r'\s*([\d，。、,.\s]+?)(?:\s*(?:USD|TWD|HKD|CNY|KRW|美元)?(?:\s|$))',
        # "raised to X from Y" or "from Y" pattern
        r'(?:raised|increased|up|cut|lowered|reduced)\s+(?:to\s+)?' + currency_prefix + r'\s*[\d，。、,.\s]+\s+from\s+' + currency_prefix + r'\s*([\d，。、,.\s]+?)(?:\s*(?:USD|TWD|HKD|CNY|KRW|美元)?(?:\s|$))',
        # "from W285,000" / "from NT$1,988" (generic)
        r'from\s+' + currency_prefix + r'\s*([\d，。、,.\s]+?)(?:\s*(?:USD|TWD|HKD|CNY|KRW|美元)?(?:\s|$))',
    ]
    for p in patterns_old:
        m = re.search(p, markdown, re.IGNORECASE)
        if m:
            try:
                val = _normalize_number(m.group(1))
                result['old'] = float(val)
                break
            except ValueError:
                continue

    # 货币检测: explicit text first, then ticker suffix, then default USD.
    result['currency'] = detect_currency(
        markdown,
        ticker=extract_ticker(markdown) or "",
        default=result['currency'],
    )

    # Sanity: TP too small — likely extracted from wrong context
    if result['new'] and result['new'] < 10:
        return None
    # Sanity: new TP is 100x+ smaller than old TP — unit error
    if result['new'] and result['old'] and result['old'] > 100:
        if result['new'] < result['old'] * 0.01:
            # new might be in different unit (thousands vs ones) — try multiplying
            if result['new'] * 1000 >= result['old'] * 0.5:
                result['new'] = result['new'] * 1000

    # 合理性校验2：过滤明显的 old TP 提取错误
    if result['new'] and result['old']:
        new, old = result['new'], result['old']
        # old < new 的 1% 且 old < 100 → 大概率误提取
        if old < new * 0.01 and old < 100:
            result['old'] = None
        # TP 变动超过 1000%，极可能误提取
        elif new > 0 and (abs(new - old) / old) > 10:
            result['old'] = None
        # old 太小 (< 5) 而 new 很大 (> 500) → 误提取
        elif old < 5 and new > 500:
            result['old'] = None

    return result if result['new'] else None


def extract_eps_forecasts(markdown: str) -> dict:
    """提取 EPS 预测：{\"FY26E\": 18.9, \"FY27E\": 39.4, ...}"""
    eps = {}

    # Find EPS table: look for header row with FY years, then EPS data row below
    # Header: "| 项目 | FY2026E | FY2027E | FY2028E |"
    # Data:   "| **调整后 EPS (韩元)** | 18,934 | 39,448 | 53,226 |"

    # Find all year columns: FY2026E, FY26E, FY3/27E, 2026E, 2025A
    fy_pattern = r'(?:FY)?(?:20)?(?:3/)?(\d{2})\d*[EA]?'

    # Strategy: find the EPS row, then look backwards for the nearest header row with years
    lines = markdown.split('\n')

    for i, line in enumerate(lines):
        if not re.search(r'(?:EPS|每股收益)', line, re.IGNORECASE):
            continue
        if '|' not in line:
            continue
        # Skip EPS revision rows (变动, YoY, 上修, 下修, 上调, growth, etc.)
        if re.search(r'(?:变动|YoY|增速|增长率|上修|下修|上调|growth|change|QoQ|环比|同比|驱动|逻辑)', line, re.IGNORECASE):
            continue
        # EPS must be in one of the first 3 columns (label may be col 1 or 2)
        clean_line = re.sub(r'\*\*', '', line)
        cols = clean_line.split('|')
        cols_to_check = [c.strip() for c in cols[1:4] if len(cols) > 1]
        if not any(re.search(r'(?:EPS|每股收益)', c, re.IGNORECASE) for c in cols_to_check):
            continue

        # Extract numbers from this row (bold markers already stripped above)
        eps_values = re.findall(r'\|?\s*([\d,.-]+)\s*(?=\||$)', clean_line)
        # Filter: skip the label column (first column)
        numeric_vals = []
        for v in eps_values:
            try:
                numeric_vals.append(float(v.replace(',', '')))
            except ValueError:
                continue

        if len(numeric_vals) < 2:
            continue

        # Find header row above (within 10 lines back) — must be a table row
        years = []
        for j in range(max(0, i-10), i):
            header_line = lines[j]
            if '|---' in header_line or '| :---' in header_line:
                continue
            if '|' not in header_line:
                continue  # skip non-table lines (prose can contain FY20xx)
            fy_matches = re.findall(fy_pattern, header_line)
            if fy_matches:
                years = fy_matches
                break

        # If no header found, try to extract years from the full text context
        if not years:
            years = re.findall(fy_pattern, '\n'.join(lines[max(0,i-20):i+5]))

        if years and len(years) >= len(numeric_vals):
            for idx, yr in enumerate(years[:len(numeric_vals)]):
                key = f'FY{yr}E'
                if key not in eps:  # keep first occurrence for duplicate years
                    eps[key] = numeric_vals[idx]
            break
        elif years:
            for idx, yr in enumerate(years):
                if idx < len(numeric_vals):
                    key = f'FY{yr}E'
                    if key not in eps:
                        eps[key] = numeric_vals[idx]
            break

    # Fallback: simple bullet pattern
    if not eps:
        simple = re.findall(r'EPS[:\s]+(\d+\.?\d*)\s*(?:[^,\n]*FY(\d{2}))?', markdown, re.IGNORECASE)
        for val, yr in simple:
            if yr:
                try:
                    eps[f'FY{yr}E'] = float(val)
                except ValueError:
                    pass

    return eps


def extract_pe_multiple(markdown: str) -> dict:
    """提取 PE 倍数：{\"current\": 29.5, \"historical_peak\": 21, \"premium_pct\": 40}"""
    result = {}

    # Pattern: "29.5倍未来十二个月 (FTM) 市盈率"
    m = re.search(r'(\d+\.?\d*)\s*倍?\s*(?:未来十二个月|FTM|forward)?\s*市[盈赢]率', markdown)
    if m:
        result['current'] = float(m.group(1))

    # Pattern: "based on 29.5x FTM P/E"
    if 'current' not in result:
        m = re.search(r'(?:based on|apply(?:ing)?|using)\s+(?:a\s+)?(\d+\.?\d*)\s*x\s*(?:FTM|forward|P/E|PE)', markdown, re.IGNORECASE)
        if m:
            result['current'] = float(m.group(1))

    # Historical peak
    m = re.search(r'(?:历史|historical).*?(\d+\.?\d*)\s*[倍x]', markdown, re.IGNORECASE)
    if m:
        result['historical_peak'] = float(m.group(1))

    # Premium/discount
    m = re.search(r'(\d+\.?\d*)\s*%\s*(?:溢价|premium)', markdown, re.IGNORECASE)
    if m:
        result['premium_pct'] = float(m.group(1))

    # P/E target: "隐含的27倍2027年预期市盈率"
    m = re.search(r'(\d+\.?\d*)\s*倍.*?(?:市盈率|P/E)', markdown)
    if m and 'current' not in result:
        result['current'] = float(m.group(1))

    return result if result else None


def extract_valuation_method(markdown: str) -> str:
    """提取估值方法：PE / Residual Income / DCF / EV/EBITDA / SOTP"""
    text = markdown[:3000]

    # Check for specific methods in order of specificity
    if re.search(r'剩余收益|Residual\s*Income|RI\s*Model|RIM', text, re.IGNORECASE):
        return 'Residual Income'
    if re.search(r'SOTP|sum.of.*?parts|分部估值|分类加总', text, re.IGNORECASE):
        return 'SOTP'
    if re.search(r'DCF|现金流折现|discounted\s*cash\s*flow', text, re.IGNORECASE):
        return 'DCF'
    if re.search(r'EV/EBITDA|企业价值.*?倍数', text, re.IGNORECASE):
        return 'EV/EBITDA'
    if re.search(r'P/B|市净率|Price.*?Book', text, re.IGNORECASE):
        return 'P/B'
    if re.search(r'PEG', text, re.IGNORECASE):
        return 'PEG'
    if re.search(r'(?:市盈率|P/E|PE\s*ratio|forward\s*PE|FTM\s*P/E)', text, re.IGNORECASE):
        return 'PE'

    return None


def extract_revenue_estimates(markdown: str) -> list:
    """提取营收预测数据"""
    estimates = []

    # 匹配 202X 年/季度 营收数据 - 支持 520B, 520 billion, TWD 520 billion
    patterns = [
        r'(?:Revenue|Sales)[^\d]*(\d{4})[^\d]*(\d+(?:[.,]\d+)?)\s*(?:billion|B|十亿|亿元)?',
        r'(\d{4})\s*(?:E|estimates?)[^\d]*(\d+(?:[.,]\d+)?)\s*(?:billion|B)',
        r'(?:FY|Q[1-4])\s*(\d{4})[^\d]*(\d+(?:[.,]\d+)?)\s*(?:billion|B|十亿)',
        r'(TWD|USD|CNY|HKD)?\s*(\d+(?:[.,]\d+)?)\s*(?:billion|B)\s*(?:revenue|sales)?',
    ]

    for p in patterns:
        matches = re.findall(p, markdown, re.IGNORECASE)
        for item in matches:
            if len(item) == 3:  # (currency, value, unit) pattern
                currency, value_str, unit = item
                year = None
            else:  # (year, value) pattern
                year, value_str = item
                currency = None

            try:
                value = float(value_str.replace(',', ''))
                # 检测单位 billion/十亿
                if 'billion' in str(item).lower() or 'B' in str(item):
                    unit = 'billion'
                else:
                    unit = 'million'
                estimates.append({
                    'year': int(year) if year else None,
                    'value': value,
                    'unit': unit,
                    'currency': currency if currency else 'USD'
                })
            except:
                pass

    return estimates


def extract_numbers(markdown: str) -> list:
    """提取所有带单位的数字"""
    numbers = []

    patterns = [
        # 百分比
        (r'(\d+(?:\.\d+)?)\s*%', 'percentage'),
        # 百万/十亿
        (r'(\d+(?:\.\d+)?)\s*(?:billion|B|十亿)', 'billion'),
        (r'(\d+(?:\.\d+)?)\s*(?:million|M|百万)', 'million'),
        # 普通数字+单位
        (r'(\d+(?:\.\d+)?)\s*(?:USD|HKD|CNY|RMB)\s*(?:billion|million)?', 'currency'),
        # 增长率
        (r'(?:YoY|同比|growth)[^\d]*(\d+(?:\.\d+)?)\s*%', 'growth_yoy'),
        (r'(?:QoQ|环比)[^\d]*(\d+(?:\.\d+)?)\s*%', 'growth_qoq'),
    ]

    for pattern, unit in patterns:
        matches = re.findall(pattern, markdown, re.IGNORECASE)
        for m in matches:
            try:
                numbers.append({'value': float(m), 'type': unit})
            except:
                pass

    return numbers


def detect_risk_signals(markdown: str) -> list:
    """检测风险信号"""
    risks = []
    # 过滤掉 section headers 和列表标记
    exclude_starts = ('risk signals', 'opportunity signals', 'key findings',
                     'risk:', 'opportunity:', '- risk', '- opportunity',
                     'table of content', 'summary')

    for keyword in RISK_KEYWORDS:
        # 找包含关键词的完整句子
        pattern = rf'[^.。\n]*{re.escape(keyword)}[^.。\n]*'
        matches = re.findall(pattern, markdown, re.IGNORECASE)
        for match in matches:
            match = match.strip()
            # 过滤：太短、太长、已包含、是 header
            if not match or len(match) < 15 or len(match) > 200:
                continue
            if match.lower().startswith(exclude_starts):
                continue
            if match not in risks:
                risks.append(match)

    return risks[:10]  # 最多10条


def detect_opportunity_signals(markdown: str) -> list:
    """检测机会信号"""
    opportunities = []
    exclude_starts = ('opportunity signals', 'risk signals', 'key findings',
                     'opportunity:', 'risk:', '- opportunity', '- risk',
                     'table of content', 'summary')

    for keyword in OPPORTUNITY_KEYWORDS:
        pattern = rf'[^.。\n]*{re.escape(keyword)}[^.。\n]*'
        matches = re.findall(pattern, markdown, re.IGNORECASE)
        for match in matches:
            match = match.strip()
            if not match or len(match) < 15 or len(match) > 200:
                continue
            if match.lower().startswith(exclude_starts):
                continue
            if match not in opportunities:
                opportunities.append(match)

    return opportunities[:10]  # 最多10条


def determine_alert_severity(rating: str, target_price_new: float = None,
                            target_price_old: float = None, risk_count: int = 0,
                            opportunity_count: int = 0) -> str:
    """判断 Alert 严重程度"""
    if rating in ['Sell', '减持', 'Underperform']:
        return 'high'
    if target_price_new and target_price_old and target_price_new < target_price_old * 0.9:
        return 'high'
    if risk_count == 0 and opportunity_count == 0:
        return 'low'  # no signals at all — don't alert
    if risk_count >= opportunity_count * 2:
        return 'medium'
    if opportunity_count > risk_count:
        return 'low'
    if risk_count > 3:
        return 'medium'
    return 'low'


# ============ 行业标签提取（三层体系）============

# 三层标签体系定义
TAG_LAYERS = {
    "sector": {  # 赛道
        "semiconductor": {"name": "半导体", "keywords": ["semiconductor", "chip", "wafer", "foundry", "fabless", "半导体", "芯片"]},
        "internet_software": {"name": "互联网/软件", "keywords": ["saas", "erp", "cloud software", "enterprise software", "软件", "互联网"]},
        "ai_application": {"name": "AI应用", "keywords": ["ai agent", "llm", "chatbot", "gpt", "gemini", "copilot", "自动驾驶"]},
        "hardware_system": {"name": "硬件/系统", "keywords": ["smartphone", "iphone", "android", "laptop", "server", "data center", "手机", "服务器"]},
        "energy_material": {"name": "能源/材料", "keywords": ["renewable", "solar", "hydrogen", "battery", "mining", "gold", "copper", "清洁能源", "光伏"]},
    },
    "value_chain": {  # 价值链位置
        "chip_design": {"name": "芯片设计", "keywords": ["fabless", "chip design", "soc", "asic design", "gpu architecture", "芯片设计"]},
        "foundry": {"name": "代工制造", "keywords": ["foundry", "wafer fab", "tsmc fab", "samsung foundry", "node", "代工", "晶圆"]},
        "packaging_test": {"name": "封装测试", "keywords": ["packaging", "cowos", "advanced packaging", "chiplet", "interposer", "封装", "测试"]},
        "equipment_material": {"name": "设备材料", "keywords": ["equipment", "wafer fab equipment", "lithography", "etch", "deposition", "substrate", "设备", "材料"]},
        "csp_datacenter": {"name": "CSP/数据中心", "keywords": ["hyperscaler", "datacenter", "cloud capex", "aws", "azure", "gcp", "数据中心", "云计算"]},
        "terminal_brand": {"name": "终端品牌", "keywords": ["apple inc.", "samsung electronics co", "xiaomi", "oppo", "vivo", "iphone", "galaxy s", "手机品牌"]},
    },
    "tech_theme": {  # 技术主题
        "ai_accelerator": {"name": "AI加速器", "keywords": ["gpu", "tpu", "asic", "ai chip", "ai accelerator", "blackwell", "rubin", "trainium", "inferentia", "maia", "AI芯片", "mi300", "mi400"]},
        "hbm_memory": {"name": "HBM/存储", "keywords": ["hbm", "hbm3", "hbm4", "dram", "nand", "memory pricing", "memory tracker", "高带宽内存", "存储芯片"]},
        "advanced_packaging": {"name": "先进封装", "keywords": ["cowos", "advanced packaging", "3d packaging", "chiplet", "hybrid bonding", "interposer", "先进封装", "CoWoS"]},
        "advanced_node": {"name": "先进制程", "keywords": ["3nm", "2nm", "n3 node", "n2 node", "gaa", "nanosheet", "finfet", "先进制程", "制程节点"]},
        "optical_interconnect": {"name": "光互连", "keywords": ["optical module", "cpo", "co-packaged optics", "serdes", "transceiver", "光模块", "光通信", "interconnect"]},
        "memory_cycle": {"name": "存储周期", "keywords": ["price hike", "contract price", "spot price", "qoq", "yoy", "asp", "涨价", "memory pricing", "price trend"]},
        "power_energy": {"name": "电源功率", "keywords": ["power semiconductor", "pmic", "sic", "gan", "mosfet", "igbt", "电源管理", "功率半导体"]},
        "consumer_electronics": {"name": "消费电子", "keywords": ["smartphone shipment", "iphone sales", "android phone", "tablet pc", "laptop pc", "消费电子", "手机出货"]},
        "auto_electronics": {"name": "汽车电子", "keywords": ["adas", "electric vehicle", "mcu automotive", "智能驾驶", "汽车芯片", "automotive mcu", "automotive semiconductor"]},
    },
}


def extract_industry_tags(markdown: str) -> dict:
    """提取三层行业标签，返回 {layer: [{slug, name, match_count, keywords}]}"""
    text_lower = markdown.lower()
    result = {}
    for layer_name, tags in TAG_LAYERS.items():
        layer_tags = []
        for slug, info in tags.items():
            matched = []
            for kw in info["keywords"]:
                if kw.lower() in text_lower:
                    matched.append(kw)
            if matched:
                layer_tags.append({
                    "slug": slug,
                    "name": info["name"],
                    "layer": layer_name,
                    "match_count": len(matched),
                    "keywords_matched": matched[:6],
                    "layer_name": {"sector": "赛道", "value_chain": "价值链", "tech_theme": "技术主题"}[layer_name]
                })
        layer_tags.sort(key=lambda t: t["match_count"], reverse=True)
        result[layer_name] = layer_tags
    return result


def parse_vision_output(markdown: str, industry: str = "general") -> dict:
    """
    主解析函数
    输入: LLM 输出的 markdown 文本
    输出: 结构化字典
    """
    result = {
        'company': extract_company(markdown),
        'ticker': extract_ticker(markdown),
        'rating': extract_rating(markdown),
        'target_price': extract_target_price(markdown),
        'eps_forecast': extract_eps_forecasts(markdown),
        'pe_multiple': extract_pe_multiple(markdown),
        'valuation_method': extract_valuation_method(markdown),
        'revenue_estimates': extract_revenue_estimates(markdown),
        'all_numbers': extract_numbers(markdown),
        'risk_signals': detect_risk_signals(markdown),
        'opportunity_signals': detect_opportunity_signals(markdown),
        'industry_tags': extract_industry_tags(markdown),
        'industry': industry,
        'parsed_at': datetime.now().isoformat()
    }

    # 计算 Alert 严重程度
    tp = result.get('target_price') or {}
    result['alert_severity'] = determine_alert_severity(
        rating=result.get('rating', ''),
        target_price_new=tp.get('new'),
        target_price_old=tp.get('old'),
        risk_count=len(result.get('risk_signals', [])),
        opportunity_count=len(result.get('opportunity_signals', []))
    )

    # 生成 Alert 标题
    if result['rating']:
        result['alert_title'] = f"{result['company']} - {result['rating']}"
    else:
        result['alert_title'] = result['company']

    return result


def parse_and_save(markdown_file: str, output_dir: str = None) -> dict:
    """
    解析 markdown 文件并保存结果
    """
    with open(markdown_file, 'r', encoding='utf-8') as f:
        markdown = f.read()

    result = parse_vision_output(markdown)

    if output_dir:
        output_path = Path(output_dir) / f"{Path(markdown_file).stem}_parsed.json"
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        return result, output_path

    return result


def print_summary(result: dict):
    """打印解析结果摘要"""
    print(f"\n{'='*60}")
    print(f"公司: {result.get('company', 'N/A')}")
    print(f"股票代码: {result.get('ticker', 'N/A')}")
    print(f"评级: {result.get('rating', 'N/A')}")

    tp = result.get('target_price')
    if tp:
        print(f"目标价: {tp.get('new', 'N/A')} {tp.get('currency', 'USD')}")
        if tp.get('old'):
            print(f"旧目标价: {tp.get('old')} (已调低)")

    print(f"\n风险信号 ({len(result.get('risk_signals', []))} 条):")
    for i, risk in enumerate(result.get('risk_signals', [])[:3], 1):
        print(f"  {i}. {risk[:80]}...")

    print(f"\n机会信号 ({len(result.get('opportunity_signals', []))} 条):")
    for i, opp in enumerate(result.get('opportunity_signals', [])[:3], 1):
        print(f"  {i}. {opp[:80]}...")

    print(f"\nAlert 严重程度: {result.get('alert_severity', 'N/A')}")
    print(f"{'='*60}\n")


# ============ 测试代码 ============
if __name__ == "__main__":
    # 测试样本
    test_markdown = """
    MediaTek Inc. (2454.TT) - Analyst Report

    Company: MediaTek Inc.
    Ticker: 2454.TT
    Rating: Buy (Outperform)
    Target Price: USD 1,450 (Previous: USD 1,280)
    Currency: TWD

    Key Findings:

    1. Rating: Buy - We maintain our Buy rating on MediaTek following
       stronger-than-expected 1Q results. The company's AI-powered
       smartphone chip business continues to gain market share.

    2. Target Price: Raised to TWD 1,450 from TWD 1,280, implying
       35% upside potential.

    3. Revenue Estimates:
       - 2026: TWD 520 billion (+18% YoY)
       - 2027: TWD 610 billion (+17% YoQ)
       - AI chip revenue to reach TWD 180 billion in 2027

    4. TPU/ASIC Business:
       - TPU shipment volume expected to grow 45% in 2026
       - AI inference chip ASP remains stable at USD 45-50

    5. Risk Signals:
       - Competition from Qualcomm intensifying in mid-range segment
       - Samsung's Exynos chip gaining market share
       - Export controls on China remain a concern
       - Global smartphone market remains weak

    6. Opportunity Signals:
       - AI smartphone upgrade cycle driving demand
       - Design wins with major Chinese OEMs
       - First-mover advantage in 3nm chip production
       - Margin expansion continuing as utilization improves

    Analysis dated: 2026-05-07
    """

    result = parse_vision_output(test_markdown, "fabless")
    print_summary(result)

    # 保存为 JSON
    with open('/tmp/test_parsed.json', 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("Saved to /tmp/test_parsed.json")
