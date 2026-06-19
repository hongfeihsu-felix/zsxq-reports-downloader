#!/usr/bin/env python3
"""
Industry Metrics Database - 行业产能/指标数据库

Schema:
  metrics        — 行业指标元数据
  data_points    — 从报告中提取的具体数据点
  time_series    — 按时间聚合的指标快照

用法：
  python3 industry_db.py extract                # 从已有分析中提取指标
  python3 industry_db.py query cowos_capacity   # 查询特定指标
  python3 industry_db.py dashboard              # 指标总览
  python3 industry_db.py --report <pdf_path>    # 从单份报告提取
"""

import os
import re
import sys
import json
import sqlite3
import argparse
from pathlib import Path
from datetime import datetime
from typing import Optional

import anthropic

PROJECT_DIR = Path(__file__).parent
DB_PATH = PROJECT_DIR / "industry_metrics.db"
REPORT_BASE = Path.home() / "hermes_reports" / "Investment_Banking_Report"

# ============ 指标定义（从 config.json 动态加载）============

CONFIG_PATH = PROJECT_DIR / "config.json"


def _load_metrics_from_config() -> list[dict]:
    """从 config.json 读取所有行业的 metrics 定义，生成 METRICS 列表。

    config 中的 metrics 格式：
      { "table": "xxx_metrics", "key_column": "...",
        "columns": { "col1": {"label": "...", "patterns": [...], "entities": [...]}, ... } }

    生成 METRICS 条目：
      { "slug": "xxx_metrics__col1", "name": "行业名 — 列标签",
        "description": "...", "unit": "", "keywords": "entity1, entity2, pattern1, pattern2",
        "table": "xxx_metrics", "column": "col1" }
    """
    if not CONFIG_PATH.exists():
        return _default_metrics()

    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    industries = cfg.get("tracking", {}).get("industries", [])
    metrics_list = []

    for ind in industries:
        if not ind.get("active", True):
            continue
        mcfg = ind.get("metrics")
        if not mcfg:
            continue

        table = mcfg.get("table", "")
        ind_name = ind.get("name", "")
        ind_keywords = ind.get("keywords", [])

        for col_name, col_def in mcfg.get("columns", {}).items():
            # Skip key columns (company, period, vendor, year) — they're labels not metrics
            if col_name in ("company", "period", "vendor", "year"):
                continue

            label = col_def.get("label", col_name)
            entities = col_def.get("entities", [])
            patterns = col_def.get("patterns", [])

            # Build keyword string from entities + patterns + partial industry keywords
            kw_parts = list(entities) + list(patterns)
            # Add up to 5 industry keywords as context
            kw_parts.extend(ind_keywords[:5])
            keywords = ", ".join(kw_parts)

            slug = f"{table}__{col_name}"

            metrics_list.append({
                "slug": slug,
                "name": f"{ind_name} — {label}",
                "description": f"{label} ({ind_name})",
                "unit": "",
                "keywords": keywords,
                "table": table,
                "column": col_name,
                "industry_slug": ind.get("slug", ""),
            })

    if not metrics_list:
        return _default_metrics()
    return metrics_list


def _default_metrics() -> list[dict]:
    """Fallback metrics when no config or no metrics defined."""
    return [
        {"slug": "cowos_capacity", "name": "CoWoS Capacity",
         "description": "TSMC CoWoS advanced packaging monthly wafer capacity",
         "unit": "kwpm", "keywords": "CoWoS, chip-on-wafer, advanced packaging capacity",
         "table": "", "column": "", "industry_slug": "cowos"},
        {"slug": "hbm_production", "name": "HBM Production",
         "description": "HBM memory production capacity or shipment volume",
         "unit": "million GB", "keywords": "HBM, HBM3, HBM3e, high bandwidth memory, production",
         "table": "", "column": "", "industry_slug": "memory"},
        {"slug": "memory_pricing", "name": "Memory Pricing Trend",
         "description": "DRAM/NAND memory pricing direction and magnitude",
         "unit": "% QoQ", "keywords": "DRAM price, memory price, NAND price, memory ASP",
         "table": "", "column": "", "industry_slug": "memory"},
        {"slug": "foundry_capex", "name": "Foundry Capex",
         "description": "Semiconductor foundry capital expenditure",
         "unit": "billion USD", "keywords": "foundry capex, fab capex, TSMC capex",
         "table": "", "column": "", "industry_slug": "foundry"},
    ]


# Build METRICS once at module load
METRICS = _load_metrics_from_config()


# ============ LLM Extraction Prompt ============

EXTRACTION_PROMPT = """You are a semiconductor industry data analyst. Extract specific industry metrics from a research report.

For each metric listed below, find the most recent numeric value, the year/quarter, the company it applies to, and a direct quote. If the metric is not mentioned, skip it.

Return ONLY valid JSON with this structure:
{{
  "metrics": [
    {{
      "slug": "metric_slug",
      "value": 123.5,
      "unit": "kwpm",
      "year": "2026",
      "quarter": "Q2",
      "company": "company name or empty string",
      "quote": "exact quote from text"
    }}
  ]
}}

Metrics to extract:
{metrics_list}

Report text:
{report_text}

Important:
- Extract ALL numbers that match each metric (one per company per year)
- For the "company" field: use the exact company name mentioned alongside each data point. If the report lists revenue for Unimicron and Nanya separately, create TWO entries with different company names. Leave empty only if the metric applies to the whole industry.
- Include the YEAR or QUARTER for each data point
- Use the exact quote from the text
- If a number is a range (e.g., "70-80 billion"), use the midpoint
- Round to 1 decimal place"""


# ============ Database ============

class IndustryDB:
    """行业指标数据库"""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path))
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS metrics (
                slug TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                unit TEXT,
                keywords TEXT
            );

            CREATE TABLE IF NOT EXISTS data_points (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                metric_slug TEXT NOT NULL,
                value REAL NOT NULL,
                unit TEXT,
                year TEXT,
                quarter TEXT,
                source_report TEXT,
                source_bank TEXT,
                source_company TEXT,
                quote TEXT,
                confidence TEXT DEFAULT 'medium',
                extracted_at TEXT NOT NULL,
                FOREIGN KEY (metric_slug) REFERENCES metrics(slug)
            );

            CREATE TABLE IF NOT EXISTS time_series (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                metric_slug TEXT NOT NULL,
                period TEXT NOT NULL,
                value REAL,
                unit TEXT,
                data_points INTEGER,
                source_count INTEGER,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (metric_slug) REFERENCES metrics(slug)
            );

            CREATE INDEX IF NOT EXISTS idx_dp_metric ON data_points(metric_slug);
            CREATE INDEX IF NOT EXISTS idx_dp_year ON data_points(year);
            CREATE INDEX IF NOT EXISTS idx_ts_metric ON time_series(metric_slug);
        """)
        self.conn.commit()
        # Auto-create dedicated industry tables from config.json
        self._ensure_industry_tables()

    def _ensure_industry_tables(self):
        """从 config.json 自动创建行业专用表。"""
        if not CONFIG_PATH.exists():
            return
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        for ind in cfg.get("tracking", {}).get("industries", []):
            mcfg = ind.get("metrics")
            if not mcfg:
                continue
            table = mcfg["table"]
            key_col = mcfg.get("key_column", "period")
            # Split composite keys like "company,period"
            key_parts = [k.strip() for k in key_col.split(",")]

            cols_sql = ["id INTEGER PRIMARY KEY AUTOINCREMENT"]
            for kp in key_parts:
                cols_sql.append(f"{kp} TEXT NOT NULL")
            for col_name in mcfg.get("columns", {}):
                if col_name in key_parts:
                    continue
                cols_sql.append(f"{col_name} REAL")
            cols_sql.append("source TEXT")
            cols_sql.append("updated_at TEXT")

            ddl = f"CREATE TABLE IF NOT EXISTS {table} ({', '.join(cols_sql)})"
            self.conn.execute(ddl)
        self.conn.commit()

    def _resolve_name(self, name: str) -> str:
        """将任意公司名匹配到 config.json 中的 canonical name。"""
        if not name or not CONFIG_PATH.exists():
            return name
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return name

        name_lower = name.lower().strip()
        for c in cfg.get("tracking", {}).get("companies", []):
            if not c.get("active", True):
                continue
            c_name_lower = c["name"].lower().strip()
            if c_name_lower == name_lower:
                return c["name"]
            if re.search(r'[一-鿿]', c_name_lower):
                if c_name_lower in name_lower:
                    return c["name"]
            elif re.search(r'\b' + re.escape(c_name_lower) + r'\b', name_lower):
                return c["name"]
            for kw in c.get("keywords", []):
                kw_lower = kw.lower().strip()
                if re.search(r'\b' + re.escape(kw_lower) + r'\b', name_lower):
                    return c["name"]
            ticker = c.get("ticker", "")
            if ticker:
                t = ticker.split(".")[0].lower()
                if t and re.search(r'\b' + re.escape(t) + r'\b', name_lower):
                    return c["name"]
        return name

    def _resolve_company(self, md_path: str) -> str:
        """从分析 MD + JSON 提取公司名，匹配到 config.json 的 canonical name。

        优先读分析 MD 的「公司：XXX」——JSON parsed.company 可能被报告中
        提到的其他公司干扰（如 Knowledge Atlas → NVIDIA）。
        """
        md_path = Path(md_path)
        json_path = md_path.parent / f"{md_path.stem.replace('_analysis', '')}_analysis.json"

        # Step 1: Try analysis MD header
        if md_path.exists():
            md_text = md_path.read_text(encoding="utf-8")[:3000]
            for pat in [r'公司[：:]\s*(?:\[公司全称\]\s*)?(.+?)(?:\n|$)',
                       r'\*\*公司[：:]\*\*\s*(.+?)(?:\n|$)']:
                m = re.search(pat, md_text)
                if m:
                    co = m.group(1).strip()
                    co = re.sub(r'[）\)］].*$', '', co).strip()
                    if co and len(co) > 1:
                        resolved = self._resolve_name(co)
                        if resolved:
                            return resolved

        # Step 2: Fallback to JSON parsed.company
        if json_path.exists():
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
                parsed = data.get("parsed", {}).get("company", "")
                if parsed:
                    resolved = self._resolve_name(parsed)
                    if resolved:
                        return resolved
            except Exception:
                pass
        return ""

    def _match_industries(self, report_text: str) -> list[str]:
        """根据报告内容匹配相关行业。返回 industry_slug 列表。"""
        if not CONFIG_PATH.exists():
            return []
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        text_lower = report_text.lower()
        matched = []
        for ind in cfg.get("tracking", {}).get("industries", []):
            if not ind.get("active", True) or "metrics" not in ind:
                continue
            keywords = ind.get("keywords", [])
            hits = sum(1 for kw in keywords if kw.lower() in text_lower)
            if hits >= 2:  # at least 2 keyword hits
                matched.append(ind["slug"])
        return matched

    def seed_metrics(self):
        """初始化指标元数据"""
        for m in METRICS:
            self.conn.execute(
                "INSERT OR REPLACE INTO metrics (slug, name, description, unit, keywords) VALUES (?, ?, ?, ?, ?)",
                (m["slug"], m["name"], m["description"], m["unit"], m["keywords"])
            )
        self.conn.commit()

    # ---- Extraction ----

    def extract_from_report(self, md_path: str, matched_industries: list[str] = None) -> list[dict]:
        """从单份分析 markdown 提取指标。

        如果提供了 matched_industries，则只提取这些行业的指标。
        """
        md_text = Path(md_path).read_text(encoding="utf-8")
        if len(md_text) > 8000:
            md_text = md_text[:8000]

        # Match industries if not provided
        if matched_industries is None:
            matched_industries = self._match_industries(md_text)

        # Filter METRICS to only matched industries
        if matched_industries:
            relevant = [m for m in METRICS if m.get("industry_slug") in matched_industries]
        else:
            relevant = METRICS

        if not relevant:
            return []

        # Build metrics list for prompt
        metrics_desc = "\n".join(
            f"- {m['slug']}: {m['description']}. Keywords: {m['keywords']}"
            for m in relevant
        )
        prompt = EXTRACTION_PROMPT.format(
            metrics_list=metrics_desc,
            report_text=md_text
        )

        client = anthropic.Anthropic(
            api_key=os.environ.get("ANTHROPIC_AUTH_TOKEN", ""),
            base_url=os.environ.get("ANTHROPIC_BASE_URL", "https://api.deepseek.com/anthropic")
        )
        model = os.environ.get("ANTHROPIC_MODEL", "deepseek-v4-pro[1m]")

        response = client.messages.create(
            model=model,
            system="You are a data extraction assistant. Return ONLY valid JSON, no other text.",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2048,
            thinking={"type": "disabled"}
        )

        text_blocks = [b.text for b in response.content if b.type == "text"]
        raw = "\n".join(text_blocks)

        # Parse JSON
        json_match = re.search(r'\{.*\}', raw, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(0)).get("metrics", [])
            except json.JSONDecodeError:
                pass
        return []

    def ingest_report(self, md_path: str, pdf_name: str = ""):
        """提取并存储报告中的指标（写入 data_points + 行业专用表）"""
        bank = ""
        if pdf_name:
            bank_m = re.match(r'^([A-Za-z\s&.]+?)[-（(]', pdf_name)
            bank = bank_m.group(1).strip() if bank_m else ""

        # Resolve company name from analysis JSON + config.json companies list
        company = self._resolve_company(md_path)

        md_text = Path(md_path).read_text(encoding="utf-8")
        matched = self._match_industries(md_text)
        data_points = self.extract_from_report(md_path, matched)
        if not data_points:
            return 0

        # Build a metric→(table, column) map from METRICS
        metric_map = {m["slug"]: (m.get("table"), m.get("column")) for m in METRICS}

        count = 0
        for dp in data_points:
            slug = dp.get("slug", "")
            value = dp.get("value")
            if not slug or value is None:
                continue

            # Write to generic data_points table
            self.conn.execute(
                """INSERT INTO data_points
                   (metric_slug, value, unit, year, quarter, source_report,
                    source_bank, source_company, quote, extracted_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (slug, value, dp.get("unit", ""), dp.get("year", ""),
                 dp.get("quarter", ""), pdf_name, bank, company,
                 dp.get("quote", "")[:500], datetime.now().isoformat())
            )

            # Also write to dedicated industry table
            table_info = metric_map.get(slug, ("", ""))
            table, column = table_info
            if table and column:
                try:
                    # Per-data-point company takes priority, then resolve against config
                    dp_company = dp.get("company", "").strip()
                    if dp_company:
                        dp_company = self._resolve_name(dp_company) or dp_company
                    row_company = dp_company or company
                    period = dp.get("year", "")
                    source = f"{bank}: {pdf_name[:60]}"

                    # UPSERT: update existing (company, period) row, or insert new
                    existing = self.conn.execute(
                        f"SELECT id FROM {table} WHERE company=? AND period=?",
                        (row_company, period)
                    ).fetchone()

                    if existing:
                        self.conn.execute(
                            f"UPDATE {table} SET {column}=?, source=?, updated_at=? WHERE id=?",
                            (value, source, datetime.now().isoformat(), existing[0])
                        )
                    else:
                        self.conn.execute(
                            f"INSERT INTO {table} (company, period, {column}, source, updated_at) "
                            f"VALUES (?, ?, ?, ?, ?)",
                            (row_company, period, value, source, datetime.now().isoformat())
                        )
                except Exception:
                    pass

            count += 1

        self.conn.commit()
        return count

    def extract_industry(self, industry_slug: str, date_str: str = None) -> dict:
        """提取指定行业的所有相关报告指标。

        使用 Matrix: industry → companies → 找包含目标公司的分析报告 → 提取。
        """
        self.seed_metrics()

        if date_str is None:
            date_str = datetime.now().strftime("%Y%m%d")
        target_dir = REPORT_BASE / date_str

        # Load Matrix to get target companies
        matrix = {}
        matrix_path = PROJECT_DIR / "industry_matrix.json"
        if matrix_path.exists():
            matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
        ind_info = matrix.get("industries", {}).get(industry_slug, {})
        target_companies = set(ind_info.get("companies", []))
        co_map = matrix.get("companies", {})

        if not target_companies:
            print(f"  No companies found for {industry_slug} in Matrix")
            return {"industry": industry_slug, "reports": 0, "data_points": 0}

        # Build company match keywords
        co_keywords = set()
        for co_name in target_companies:
            co_keywords.add(co_name.lower())
            co_info = co_map.get(co_name, {})
            ticker = co_info.get("ticker", "").split(".")[0].lower()
            if ticker:
                co_keywords.add(ticker)

        print(f"  [{industry_slug}] Target companies: {len(target_companies)}")

        # Find reports by company match
        results = {"industry": industry_slug, "reports": 0, "data_points": 0}
        md_files = sorted(target_dir.glob("*_analysis.md")) if target_dir.is_dir() else []

        # Also search previous 3 days for broader coverage
        for offset in range(4):
            search_dir = REPORT_BASE / (datetime.now() - __import__('datetime').timedelta(days=offset)).strftime("%Y%m%d")
            if search_dir.is_dir() and search_dir != target_dir:
                md_files.extend(sorted(search_dir.glob("*_analysis.md")))

        for mf in md_files:
            content = mf.read_text(encoding="utf-8")[:5000]
            content_lower = content.lower()
            if not any(co_kw in content_lower for co_kw in co_keywords):
                continue

            pdf_name = mf.stem.replace("_analysis", "")
            print(f"  📊 [{industry_slug}] {pdf_name[:55]}...")
            count = self.ingest_report(str(mf), pdf_name)
            if count > 0:
                results["reports"] += 1
                results["data_points"] += count

        self._update_time_series()
        return results

    def extract_all(self) -> dict:
        """从所有已有分析中批量提取"""
        self.seed_metrics()

        results = {"total": 0, "reports": 0, "data_points": 0}
        for f in sorted(REPORT_BASE.rglob("*_analysis.md")):
            pdf_name = f.stem.replace("_analysis", "")
            print(f"  📊 Extracting: {pdf_name[:55]}...")
            count = self.ingest_report(str(f), pdf_name)
            if count > 0:
                results["reports"] += 1
                results["data_points"] += count
                print(f"     {count} data points")
            results["total"] += 1

        self._update_time_series()
        return results

    # ---- Time Series ----

    def _update_time_series(self):
        """聚合 data_points 到 time_series（按 year 分组）"""
        self.conn.execute("DELETE FROM time_series")

        rows = self.conn.execute("""
            SELECT metric_slug, year,
                   AVG(value) as avg_value,
                   MIN(value) as min_value,
                   MAX(value) as max_value,
                   COUNT(*) as data_points,
                   COUNT(DISTINCT source_report) as source_count,
                   GROUP_CONCAT(DISTINCT unit) as units
            FROM data_points
            WHERE year != '' AND value > 0
            GROUP BY metric_slug, year
            ORDER BY metric_slug, year
        """).fetchall()

        for row in rows:
            self.conn.execute(
                """INSERT INTO time_series (metric_slug, period, value, unit, data_points, source_count, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (row[0], row[1], row[2], row[7] or "", row[5], row[6], datetime.now().isoformat())
            )
        self.conn.commit()

    # ---- Query ----

    def query_metric(self, slug: str) -> list[dict]:
        """查询某指标的所有数据点"""
        rows = self.conn.execute(
            """SELECT metric_slug, value, unit, year, quarter, source_bank, source_company, quote
               FROM data_points WHERE metric_slug = ? ORDER BY year, quarter""",
            (slug,)
        ).fetchall()

        return [
            {
                "slug": r[0], "value": r[1], "unit": r[2], "year": r[3],
                "quarter": r[4], "source_bank": r[5], "source_company": r[6], "quote": r[7]
            }
            for r in rows
        ]

    def query_time_series(self, slug: str = None) -> list[dict]:
        """查询时间序列"""
        if slug:
            rows = self.conn.execute(
                "SELECT * FROM time_series WHERE metric_slug = ? ORDER BY period", (slug,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM time_series ORDER BY metric_slug, period"
            ).fetchall()
        return [
            {"slug": r[1], "period": r[2], "value": r[3], "unit": r[4],
             "data_points": r[5], "source_count": r[6]}
            for r in rows
        ]

    def dashboard(self):
        """指标总览"""
        metrics = self.conn.execute("SELECT * FROM metrics").fetchall()
        if not metrics:
            print("No metrics defined. Run 'seed' first.")
            return

        print(f"\n{'=' * 70}")
        print(f"  📊 Industry Metrics Dashboard")
        print(f"{'=' * 70}")

        for m in metrics:
            slug, name, desc, unit, keywords = m
            count = self.conn.execute(
                "SELECT COUNT(*) FROM data_points WHERE metric_slug = ?", (slug,)
            ).fetchone()[0]

            ts_count = self.conn.execute(
                "SELECT COUNT(*) FROM time_series WHERE metric_slug = ?", (slug,)
            ).fetchone()[0]

            bar = "█" * min(count, 30)
            print(f"\n  ┌─ {name}  [{slug}]")
            print(f"  │  {bar} {count} data points | {ts_count} time periods")
            print(f"  │  Unit: {unit}")

            # Recent values
            recent = self.conn.execute(
                """SELECT year, value, unit, source_bank FROM data_points
                   WHERE metric_slug = ? ORDER BY year DESC LIMIT 5""",
                (slug,)
            ).fetchall()
            if recent:
                print(f"  │  Recent:")
                for r in recent:
                    print(f"  │    {r[0]}  {r[1]:>10,.1f} {r[2]:<10} ({r[3]})")

        print(f"\n{'=' * 70}\n")

    def close(self):
        self.conn.close()


# ============ CLI ============

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Industry Metrics Database")
    sub = parser.add_subparsers(dest="cmd", help="Commands")

    sub.add_parser("seed", help="Initialize metric definitions")
    extract_cmd = sub.add_parser("extract", help="Extract metrics from analyses")
    extract_cmd.add_argument("--industry", help="Industry slug (e.g., abf-substrate, pcb-ccl)")
    sub.add_parser("dashboard", help="Show metrics dashboard")

    query_cmd = sub.add_parser("query", help="Query a specific metric")
    query_cmd.add_argument("slug", help="Metric slug (e.g., cowos_capacity)")

    ts_cmd = sub.add_parser("ts", help="Show time series")
    ts_cmd.add_argument("slug", nargs="?", help="Metric slug (optional, all if omitted)")

    report_cmd = sub.add_parser("report", help="Extract from a single report")
    report_cmd.add_argument("path", help="Path to _analysis.md file")

    args = parser.parse_args()
    db = IndustryDB()

    if args.cmd == "seed":
        db.seed_metrics()
        print(f"✅ Seeded {len(METRICS)} metric definitions")

    elif args.cmd == "extract":
        if hasattr(args, 'industry') and args.industry:
            print(f"\n🔬 Extracting metrics for industry: {args.industry}...")
            results = db.extract_industry(args.industry)
            print(f"\n✅ Processed {results['reports']} reports, "
                  f"extracted {results['data_points']} data points for {args.industry}")
        else:
            print(f"\n🔬 Extracting metrics from all analyses...")
            results = db.extract_all()
            print(f"\n✅ Processed {results['total']} reports, "
                  f"extracted {results['data_points']} data points from {results['reports']} reports")

    elif args.cmd == "dashboard":
        db.dashboard()

    elif args.cmd == "query":
        data = db.query_metric(args.slug)
        if not data:
            print(f"No data for metric: {args.slug}")
        else:
            print(f"\n{'Year':<6} {'Q':<3} {'Value':>12} {'Unit':<12} {'Bank':<20} {'Company'}")
            print(f"{'─'*6} {'─'*3} {'─'*12} {'─'*12} {'─'*20} {'─'*20}")
            for d in data:
                print(f"{d['year']:<6} {d['quarter']:<3} {d['value']:>12,.1f} "
                      f"{d['unit']:<12} {d['source_bank']:<20} {d['source_company']}")

    elif args.cmd == "ts":
        data = db.query_time_series(args.slug)
        for d in data:
            print(f"{d['slug']:<25} {d['period']:<8} {d['value']:>10,.1f} {d['unit']:<12} "
                  f"({d['data_points']} pts, {d['source_count']} sources)")

    elif args.cmd == "report":
        count = db.ingest_report(args.path, Path(args.path).stem)
        print(f"✅ Extracted {count} data points from {args.path}")

    else:
        parser.print_help()

    db.close()
