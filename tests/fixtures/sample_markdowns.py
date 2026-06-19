"""Test fixtures — inline markdown samples and known report snapshots."""

# ---- EPS extraction markdowns ----

EPS_TABLE_JP = """
| 项目 | FY2026E | FY2027E | FY2028E |
| :--- | :--- | :--- | :--- |
| **营收 (十亿韩元)** | 13,763 | 18,380 | 22,121 |
| **调整后 EPS (韩元)** | 18,934 | 39,448 | 53,226 |
| **调整后 EPS YoY%** | +109.1% | +108.3% | +34.9% |
"""

EPS_TABLE_TWD = """
| 项目 | FY2025A | FY2026E | FY2027E | FY2028E |
| :--- | :--- | :--- | :--- | :--- |
| **稀释后 EPS (NT$)** | 4.36 | 14.06 | 25.10 | 36.60 |
"""

EPS_TABLE_SIMPLE = """
| FY2026E | FY2027E |
|---|---|
| **EPS** | 5.50 | 7.20 |
"""

EPS_BULLET = """
Key estimates:
- Revenue: $12.5B (FY26E)
- EPS: 3.45 (FY26E), 4.20 (FY27E)
- Target Price: $85
"""

EPS_NEGATIVE = """
| 项目 | FY2026E | FY2027E |
| :--- | :--- | :--- |
| **EPS (USD)** | -0.50 | 1.20 |
"""

NO_EPS = """
This report discusses market trends but does not include specific EPS forecasts.
Revenue growth is expected to be 15% in FY2026.
"""

# ---- PE multiple markdowns ----

PE_CHINESE_FTM = """基于 29.5 倍未来十二个月 (FTM) 市盈率进行估值。
该估值倍数较 2016-2018 年周期中 21 倍的历史峰值市盈率存在 40% 的溢价。"""

PE_ENGLISH = """Our price target is based on a 29.5x FTM P/E, applying a 40% premium to
the historical peak of 21x. """

PE_IMPLIED = """目标价隐含的 27 倍 2027 年预期市盈率"""

PE_NONSTANDARD = """We apply a 15.5x target P/E multiple to our FY27E EPS estimate."""

PE_NO_MENTION = """The stock is attractively valued relative to peers. We maintain our Buy rating."""

# ---- Valuation method markdowns ----

METHOD_PE = """采用市盈率 (P/E) 估值法，基于 25 倍 FY27E EPS。"""

METHOD_DCF = """We use a DCF model with 9.5% WACC and 3% terminal growth."""

METHOD_RI = """采用剩余收益估值模型 (Residual Income Model)，权益成本 9.2%，中期增长率 15%。"""

METHOD_SOTP = """SOTP valuation suggests a fair value of $120 per share."""

METHOD_EV_EBITDA = """EV/EBITDA multiple of 12x applied to FY27E EBITDA of $8.5B."""

METHOD_PB = """P/B ratio of 2.5x applied to FY27E book value per share of $45."""

METHOD_MULTI = """We value the company using a combination of DCF and P/E approaches."""

METHOD_NONE = """The company reported strong Q1 results. Revenue grew 25% YoY."""

# ---- Target price edge cases ----

TP_KOREAN_WON = """**目标价**：460,000 韩元 (此前：320,000)"""

TP_NO_TP = """Target Price: Not applicable (no target price provided in this update)."""

TP_SINGLE = """Target Price: 850 TWD"""

TP_RAISED = """raised to 5000 from 2454"""

TP_FILTER_TOO_SMALL = """Target Price: 3 (penny stock under $5)"""  # Should be filtered

# ---- Full report snapshots (for consensus tests) ----

MEDIATEK_REPORT_1 = {
    "bank": "Goldman Sachs",
    "rating": "Buy",
    "tp_new": 5000.0, "tp_old": 2454.0, "tp_currency": "TWD",
    "eps_forecast": {"FY25E": 66.17, "FY26E": 63.29, "FY27E": 132.18, "FY28E": 406.51},
    "pe": 25.0, "method": "PE",
}

MEDIATEK_REPORT_2 = {
    "bank": "Morgan Stanley",
    "rating": "Overweight",
    "tp_new": 4200.0, "tp_old": None, "tp_currency": "TWD",
    "eps_forecast": {"FY26E": 68.00, "FY27E": 110.01},
    "pe": 30.0, "method": "Residual Income",
}

MEDIATEK_REPORT_3 = {
    "bank": "J.P. Morgan",
    "rating": "Overweight",
    "tp_new": 5088.0, "tp_old": 3500.0, "tp_currency": "TWD",
    "eps_forecast": {"FY26E": 70.02, "FY27E": 103.91},
    "pe": 38.0, "method": "PE",
}

MEDIATEK_REPORT_4 = {
    "bank": "Nomura",
    "rating": "Buy",
    "tp_new": 3050.0, "tp_old": 2800.0, "tp_currency": "TWD",
    "eps_forecast": {"FY25E": 65.71, "FY26E": 62.06, "FY27E": 85.73},
    "pe": None, "method": "PE",
}

# Three reports with reasonable EPS values (for no-outlier consensus test)
CLEAN_EPS_REPORTS = [
    {"bank": "BankA", "rating": "Buy", "tp_new": 100.0, "tp_currency": "USD",
     "eps_forecast": {"FY26E": 10.0, "FY27E": 12.0}, "pe": 15.0, "method": "PE"},
    {"bank": "BankB", "rating": "Hold", "tp_new": 110.0, "tp_currency": "USD",
     "eps_forecast": {"FY26E": 11.0, "FY27E": 11.5}, "pe": 12.0, "method": "PE"},
    {"bank": "BankC", "rating": "Buy", "tp_new": 105.0, "tp_currency": "USD",
     "eps_forecast": {"FY26E": 9.5, "FY27E": 12.5, "FY28E": 15.0}, "pe": 18.0, "method": "DCF"},
]

# ---- EPS: second-column label (KR Memory Tracker) ----
EPS_SECOND_COL = """
| Company | Metric | 2025A | 2026E | 2027E |
|---|---|---|---|---|
| Samsung (005930.KS) | Reported EPS (KRW) | 6,611.53 | 35,740 | 49,548 |
| SK hynix (000660.KS) | Reported EPS (KRW) | 60,341 | 286,732 | 385,594 |
"""

# ---- EPS: Japanese fiscal year FY3/27E ----
EPS_JP_FY = """
### FY3/27 至 FY3/29 预测
| 指标 | FY3/27E (新) | FY3/28E (新) | FY3/29E (新) |
| :--- | :--- | :--- | :--- |
| **每股收益 EPS (¥)** | 8,408.2 | 8,113.7 | 8,709.2 |
"""

# ---- EPS: prose format (US reports) ----
EPS_PROSE = """
The company reported non-GAAP earnings per share of $4.77 in FY2026,
with estimates of $9.19 for FY2027 and $12.52 for FY2028.
"""

# ---- PE: false positive patterns ----
PE_FALSE_POSITIVE_1 = "The stock trades at a discount with a P/B of 2.0x book value."
PE_FALSE_POSITIVE_2 = "Revenue growth reached 37.0% in the most recent quarter."
