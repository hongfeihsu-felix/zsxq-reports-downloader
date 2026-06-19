#!/usr/bin/env python3
"""
Industry Structured Databases — HBM / CoWoS / Memory Pricing

专用数据库表，替代松散的数据点存储：
  hbm_capacity      — 各厂 HBM TSV 产能 (K wpm) + 供需比
  cowos_capacity    — CoWoS 产能 (wpm) + 各厂 booking
  memory_pricing    — DRAM/NAND 合约+现货价格时间序列

用法：
  python3 industry_data.py seed           # 初始化表 + 导入参考数据
  python3 industry_data.py show hbm       # 查看 HBM 数据
  python3 industry_data.py show cowos     # 查看 CoWoS 数据
  python3 industry_data.py show memory    # 查看 Memory 价格
"""

import json
import sqlite3
import argparse
from pathlib import Path
from datetime import datetime

DB_PATH = Path(__file__).parent / "industry_metrics.db"


def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.executescript("""
        -- HBM TSV 产能 (K wpm)
        CREATE TABLE IF NOT EXISTS hbm_capacity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor TEXT NOT NULL,           -- SK Hynix / Samsung / Micron
            year INTEGER NOT NULL,
            tsv_kwpm REAL,                 -- TSV 产能 (千片/月)
            yield_pct REAL,                -- 良率 %
            utr_pct REAL,                  -- 利用率 %
            hbm_output_mn_gb REAL,         -- HBM 产出 (百万 Gb/年)
            hbm_output_tb REAL,            -- HBM 产出 (TB/年)
            source TEXT,                   -- 来源报告
            updated_at TEXT,
            UNIQUE(vendor, year)
        );

        -- HBM 供需汇总
        CREATE TABLE IF NOT EXISTS hbm_supply_demand (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            year INTEGER NOT NULL UNIQUE,
            hbm_demand_mn_gb REAL,         -- HBM 需求 (百万 Gb)
            hbm_supply_mn_gb REAL,         -- HBM 供给 (百万 Gb)
            sufficiency_pct REAL,          -- 供需比 %
            source TEXT,
            updated_at TEXT
        );

        -- CoWoS 产能 + Booking
        CREATE TABLE IF NOT EXISTS cowos_capacity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,           -- 2023H1 / 2025 / 2026E
            total_wpm REAL,                -- 总产能 (wafers/month)
            effective_wpm REAL,            -- 有效产能 (良率调整后)
            nvidia_wpm REAL,               -- NVIDIA booking
            google_wpm REAL,               -- Google TPU booking
            broadcom_wpm REAL,             -- Broadcom booking
            mediatek_wpm REAL,             -- MediaTek booking (Google TPU foundry)
            amd_wpm REAL,                  -- AMD booking
            intel_wpm REAL,                -- Intel booking
            other_wpm REAL,                -- 其他 booking
            csp_note TEXT,                 -- CSP 原厂备注
            source TEXT,
            updated_at TEXT,
            UNIQUE(period)
        );

        -- Memory 价格时间序列
        CREATE TABLE IF NOT EXISTS memory_pricing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,           -- 2024-Q1 / 2025-Q4 / 2026-Q1
            product TEXT NOT NULL,          -- DDR5_16Gb / HBM3e_8Hi / NAND_wafer
            price REAL,                    -- 合约价 (USD)
            qoq_pct REAL,                  -- QoQ 涨幅 %
            yoy_pct REAL,                  -- YoY 涨幅 %
            price_type TEXT DEFAULT 'contract', -- contract / spot
            source TEXT,
            updated_at TEXT,
            UNIQUE(period, product, price_type)
        );

        CREATE INDEX IF NOT EXISTS idx_hbm_year ON hbm_capacity(year);
        CREATE INDEX IF NOT EXISTS idx_cowos_period ON cowos_capacity(period);
        CREATE INDEX IF NOT EXISTS idx_memory_period ON memory_pricing(period);
    """)
    conn.commit()
    return conn


# ============ HBM Data ============

HBM_CAPACITY_SEED = [
    # MS Model (Jan 2026)
    ("SK Hynix", 2023, 45, 60, 100, 1500, None, "Morgan Stanley Global Tech Outlook 2026"),
    ("SK Hynix", 2024, 120, 60, 100, 6273, None, "Morgan Stanley Global Tech Outlook 2026"),
    ("SK Hynix", 2025, 150, 75, 100, 12830, 1579, "Morgan Stanley Global Tech Outlook 2026"),
    ("SK Hynix", 2026, 200, 70, 100, 15072, 1855, "Morgan Stanley Global Tech Outlook 2026"),
    ("Samsung", 2023, 45, 50, 80, 1500, None, "Morgan Stanley Global Tech Outlook 2026"),
    ("Samsung", 2024, 130, 50, 80, 4435, None, "Morgan Stanley Global Tech Outlook 2026"),
    ("Samsung", 2025, 150, 60, 60, 6387, 786, "Morgan Stanley Global Tech Outlook 2026"),
    ("Samsung", 2026, 220, 60, 70, 9560, 1176, "Morgan Stanley Global Tech Outlook 2026"),
    ("Micron", 2023, 3, 50, 100, 150, None, "Morgan Stanley Global Tech Outlook 2026"),
    ("Micron", 2024, 20, 50, 100, 729, None, "Morgan Stanley Global Tech Outlook 2026"),
    ("Micron", 2025, 60, 70, 100, 3548, 437, "Morgan Stanley Global Tech Outlook 2026"),
    ("Micron", 2026, 90, 70, 100, 6459, 795, "Morgan Stanley Global Tech Outlook 2026"),
]

HBM_SUPPLY_DEMAND_SEED = [
    (2023, 1866, 3150, 69, "Morgan Stanley Global Tech Outlook 2026"),
    (2024, 9059, 11436, 26, "Morgan Stanley Global Tech Outlook 2026"),
    (2025, 19161, 22765, 19, "Morgan Stanley Global Tech Outlook 2026"),
    (2026, 30565, 31091, 2, "Morgan Stanley Global Tech Outlook 2026"),
]


# ============ CoWoS Data ============

COWOS_CAPACITY_SEED = [
    # period, total_wpm, effective, nvidia, google, broadcom, mediatek, amd, intel, other, csp_note, source
    ("2023", 13000, 11000, 9000, 1000, 500, 0, 300, 0, 200, "早期产能", "TSMC法说会+TrendForce"),
    ("2024H1", 35000, 30000, 24500, 3700, 1200, 0, 400, 0, 200, "快速扩张", "TSMC法说会+TrendForce"),
    ("2025E", 65000, 55000, 38500, 7400, 3600, 500, 1500, 0, 2000, "紧平衡; MTK 起步", "TSMC法说会"),
    ("2025E_detail", 30000, None, 21000, 4500, 2400, 400, 1000, 0, 700, "CoWoS-S主力; CSP: Google 15%; MTK TPU v7 起步", "Bernstein+BofA"),
    # Updated 2026-05-17 from JPM CoWoS Report
    ("2026E", 115000, 92000, 60000, None, 21000, 3000, 7000, 3000, None,
     "年终115K wpm; 年产出1.1M; GB300+Vera+SpectrumX; AMD MI450; MTK TPU v7", "JPM May-26"),
    ("2027E", 175000, 145000, 93000, None, 35000, 10000, 15000, 5000, None,
     "年终175K wpm; 年产出1.9M; Rubin+MI450 ramp; MTK TPU v8爆量; CoWoS延伸14x光罩",
     "JPM May-26"),
    ("2028E", 220000, 180000, None, None, None, None, None, None, None,
     "年终220K wpm; 年产出2.4M; CoPoS延迟至2029-2030放量; CoWoS生命周期延长",
     "JPM May-26"),
]


# ============ Memory Pricing Data ============

MEMORY_PRICING_SEED = [
    # DDR5 16Gb 合约价 (USD/GB)
    ("2024-Q1", "DDR5_16Gb_chip", 4.0, None, None, "contract", "TrendForce+BofA"),
    ("2024-Q4", "DDR5_16Gb_chip", 8.0, 38, 129, "contract", "TrendForce+BofA"),
    ("2025-Q1", "DDR5_16Gb_chip", 20.0, 150, 400, "contract", "TrendForce+BofA"),
    ("2025-Q4", "DDR5_16Gb_chip", 38.0, 26, 200, "contract", "TrendForce+BofA"),
    ("2026-Q1", "DDR5_16Gb_chip", 38.0, 93, 200, "contract", "TrendForce+BofA"),
    ("2026-Q2E", "DDR5_16Gb_chip", 42.0, 20, 180, "contract", "TrendForce预估"),

    # PC DDR4 8Gb 合约价
    ("2025-Q4", "DDR4_8Gb_chip", 8.1, None, None, "contract", "Bernstein Memory Tracker Apr"),
    ("2026-Q1", "DDR4_8Gb_chip", 12.5, 53.7, None, "contract", "Bernstein Memory Tracker Apr"),
    ("2026-Q2E", "DDR4_8Gb_chip", 16.0, 28, None, "contract", "Bernstein Memory Tracker Apr"),

    # HBM3e 8-Hi 合约价
    ("2025-Q1", "HBM3e_8Hi", 20.0, 20, None, "contract", "TrendForce"),
    ("2025-Q4", "HBM3e_8Hi", 38.0, 26, None, "contract", "TrendForce"),
    ("2026-Q1", "HBM3e_8Hi", 45.0, 18, None, "contract", "CLSA+TrendForce"),

    # NAND Wafer 合约价
    ("2025-Q4", "NAND_wafer", None, None, None, "contract", "自2025.9累计涨5-7x"),
    ("2026-Q1", "NAND_wafer", None, None, None, "contract", "+22~25% QoQ (Bernstein)"),
    ("2026-Q2E", "NAND_wafer", None, 65, None, "contract", "+65-70% QoQ (Bernstein)"),

    # NAND eMMC/UFS
    ("2026-Q2E", "NAND_eMMC_UFS", None, 75, None, "contract", "+75-80% QoQ (Bernstein)"),

    # LPDDR5 16GB Mobile
    ("2025-Q4", "LPDDR5_16GB", 4.0, None, None, "contract", "Bernstein Memory Tracker Apr"),
    ("2026-Q1", "LPDDR5_16GB", 6.3, 58.3, None, "contract", "Bernstein Memory Tracker Apr"),
    ("2026-Q2E", "LPDDR5_16GB", None, 80, None, "contract", "+~80% QoQ (Bernstein)"),

    # Server DDR5 96GB RDIMM
    ("2025-Q4", "Server_DDR5_96GB_RDIMM", 6.9, None, None, "contract", "Bernstein Memory Tracker Apr"),
    ("2026-Q1", "Server_DDR5_96GB_RDIMM", 13.9, 100, None, "contract", "Bernstein Memory Tracker Apr"),
    ("2026-Q2E", "Server_DDR5_96GB_RDIMM", 20.4, 47.5, None, "contract", "Bernstein Memory Tracker Apr"),
]


def cmd_seed():
    """初始化表 + 导入参考数据"""
    conn = init_db()
    now = datetime.now().isoformat()
    count = 0

    # HBM Capacity
    conn.execute("DELETE FROM hbm_capacity")
    for row in HBM_CAPACITY_SEED:
        vendor, year, tsv, yld, utr, gb_out, tb_out, src = row
        conn.execute(
            """INSERT OR REPLACE INTO hbm_capacity
               (vendor, year, tsv_kwpm, yield_pct, utr_pct, hbm_output_mn_gb, hbm_output_tb, source, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (vendor, year, tsv, yld, utr, gb_out, tb_out, src, now)
        )
        count += 1
    print(f"  HBM Capacity: {count} rows")

    # HBM Supply/Demand
    conn.execute("DELETE FROM hbm_supply_demand")
    count = 0
    for row in HBM_SUPPLY_DEMAND_SEED:
        year, demand, supply, suff, src = row
        conn.execute(
            """INSERT OR REPLACE INTO hbm_supply_demand
               (year, hbm_demand_mn_gb, hbm_supply_mn_gb, sufficiency_pct, source, updated_at)
               VALUES (?,?,?,?,?,?)""",
            (year, demand, supply, suff, src, now)
        )
        count += 1
    print(f"  HBM Supply/Demand: {count} rows")

    # CoWoS Capacity
    conn.execute("DELETE FROM cowos_capacity")
    count = 0
    for row in COWOS_CAPACITY_SEED:
        period, total, eff, nv, goog, bcom, mtk, amd, intel, other, csp_note, src = row
        conn.execute(
            """INSERT OR REPLACE INTO cowos_capacity
               (period, total_wpm, effective_wpm, nvidia_wpm, google_wpm, broadcom_wpm, mediatek_wpm, amd_wpm, intel_wpm, other_wpm, csp_note, source, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (period, total, eff, nv, goog, bcom, mtk, amd, intel, other, csp_note, src, now)
        )
        count += 1
    print(f"  CoWoS Capacity: {count} rows")

    # Memory Pricing
    conn.execute("DELETE FROM memory_pricing")
    count = 0
    for row in MEMORY_PRICING_SEED:
        period, product, price, qoq, yoy, ptype, src = row
        conn.execute(
            """INSERT OR REPLACE INTO memory_pricing
               (period, product, price, qoq_pct, yoy_pct, price_type, source, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (period, product, price, qoq, yoy, ptype, src, now)
        )
        count += 1
    print(f"  Memory Pricing: {count} rows")

    conn.commit()
    conn.close()
    print(f"\n✅ Industry databases seeded ({datetime.now().strftime('%Y-%m-%d')})")


def cmd_show(table: str):
    """显示数据库内容"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    if table == "hbm":
        print("\n=== HBM TSV 产能 (K wpm) ===")
        rows = conn.execute("SELECT * FROM hbm_capacity ORDER BY year, vendor").fetchall()
        print(f"{'Vendor':<12} {'Year':>4} {'TSV_K':>7} {'Yield%':>7} {'UTR%':>6} {'Gb/yr':>10} {'TB/yr':>8}  Source")
        print("-" * 80)
        for r in rows:
            tb = f"{r['hbm_output_tb']:,.0f}" if r['hbm_output_tb'] else ""
            print(f"{r['vendor']:<12} {r['year']:>4} {r['tsv_kwpm']:>7,.0f} {r['yield_pct']:>6.0f}% {r['utr_pct']:>5.0f}% {r['hbm_output_mn_gb']:>10,.0f} {tb:>8}  {r['source'][:40]}")

        print("\n=== HBM 供需比 ===")
        rows = conn.execute("SELECT * FROM hbm_supply_demand ORDER BY year").fetchall()
        print(f"{'Year':>4} {'Demand(Mn Gb)':>14} {'Supply(Mn Gb)':>14} {'Sufficiency':>12}")
        print("-" * 50)
        for r in rows:
            print(f"{r['year']:>4} {r['hbm_demand_mn_gb']:>14,.0f} {r['hbm_supply_mn_gb']:>14,.0f} {r['sufficiency_pct']:>10,.0f}%")

    elif table == "cowos":
        print("\n=== CoWoS 产能 + Booking ===")
        rows = conn.execute("SELECT * FROM cowos_capacity ORDER BY period").fetchall()
        print(f"{'Period':<12} {'Total':>7} {'Eff':>7} {'NVDA':>7} {'Google':>7} {'BCOM':>7} {'MTK':>7} {'AMD':>7} {'Intel':>7} {'Other':>7}  CSP Note")
        print("-" * 105)
        for r in rows:
            note = (r['csp_note'] or '')[:40]
            print(f"{r['period']:<12} {r['total_wpm'] or '':>7} {r['effective_wpm'] or '':>7} {r['nvidia_wpm'] or '':>7} {r['google_wpm'] or '':>7} {r['broadcom_wpm'] or '':>7} {r['mediatek_wpm'] or '':>7} {r['amd_wpm'] or '':>7} {r['intel_wpm'] or '':>7} {r['other_wpm'] or '':>7}  {note}")

    elif table == "memory":
        print("\n=== Memory 价格时间序列 ===")
        rows = conn.execute("SELECT * FROM memory_pricing ORDER BY product, period").fetchall()
        print(f"{'Period':<10} {'Product':<25} {'Price':>8} {'QoQ%':>8} {'YoY%':>8} {'Type':<10} Source")
        print("-" * 90)
        for r in rows:
            price = f"${r['price']:.1f}" if r['price'] else ""
            qoq = f"{r['qoq_pct']:+.0f}%" if r['qoq_pct'] is not None else ""
            yoy = f"{r['yoy_pct']:+.0f}%" if r['yoy_pct'] is not None else ""
            print(f"{r['period']:<10} {r['product']:<25} {price:>8} {qoq:>8} {yoy:>8} {r['price_type']:<10} {r['source'][:30]}")

    conn.close()


def _load_industry_metrics_config() -> dict[str, dict]:
    """Load industry metrics extraction config from config.json."""
    cfg_path = Path(__file__).parent / "config.json"
    if not cfg_path.exists():
        return {}
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    industries = cfg.get("tracking", {}).get("industries", [])
    return {
        ind["slug"]: ind
        for ind in industries
        if ind.get("active") and ind.get("metrics")
    }


def _match_industry(chain: dict, industry_configs: dict[str, dict]) -> list[str]:
    """Return slugs of industries whose keywords match this logic chain."""
    driver = chain.get("driver", "").lower()
    all_text = driver
    for ev in chain.get("evidence", []):
        all_text += " " + ev.get("metric", "").lower()
        all_text += " " + ev.get("value", "").lower()

    matched = []
    for slug, ind in industry_configs.items():
        for kw in ind.get("keywords", []):
            if kw.lower() in all_text:
                matched.append(slug)
                break
    return matched


def _parse_number(value: str) -> float | None:
    """Extract a numeric value from a Chinese/English value string.
    Returns the raw number (may need unit conversion)."""
    import re
    # Clean: remove parentheses, Chinese brackets, whitespace
    cleaned = re.sub(r'[（(].*?[)）]', '', value)
    # Try comma-separated number first: 1,120,000
    m = re.search(r'(\d{1,3}(?:,\d{3})+(?:\.\d+)?)', cleaned)
    if m:
        return float(m.group(1).replace(",", ""))
    # Plain number
    m = re.search(r'(\d+(?:\.\d+)?)', cleaned)
    if m:
        return float(m.group(1))
    return None


def _parse_period(text: str) -> str | None:
    """Extract period from text. Returns '2026E', '2027H1', etc."""
    import re
    year_m = re.search(r'(20\d{2})', text)
    if not year_m:
        return None
    year = year_m.group(1)
    # Check for E/F/Y suffix (estimate/forecast)
    suffix = "E"  # default to estimate for forward-looking data
    if re.search(rf'{year}\s*[Hh]1|{year}\s*上', text):
        return f"{year}H1"
    if re.search(rf'{year}\s*[Hh]2|{year}\s*下', text):
        return f"{year}H2"
    return f"{year}E"


def _convert_to_wpm(value: float, value_text: str) -> float:
    """Convert raw value to wpm (wafers per month)."""
    text = value_text.lower()
    # 万片/年 → /12
    if ("万" in value_text or "万" in text) and ("年" in value_text or "annual" in text):
        return value * 10000 / 12
    # 千片/年 → *1000/12
    if ("千" in value_text or "k" in text) and ("/年" in value_text or "/yr" in text):
        return value * 1000 / 12
    # 片/年 (raw wafers/year) → /12
    if "片/年" in value_text or "/yr" in text or "annual" in text:
        return value / 12
    # Already wpm or unclear — return as-is
    return value


def _extract_industry(conn, chain: dict, industry_cfg: dict, now: str) -> int:
    """Generic industry metric extractor driven by config.

    For each evidence item in the logic chain:
      1. Extract period (year)
      2. Extract numeric value
      3. Match to a DB column via entity_patterns or metric patterns
      4. Convert units if needed
      5. Upsert into the configured table
    """
    import re

    metrics_cfg = industry_cfg.get("metrics", {})
    table = metrics_cfg.get("table")
    key_col = metrics_cfg.get("key_column", "period")
    columns = metrics_cfg.get("columns", {})
    if not table or not columns:
        return 0

    driver = chain.get("driver", "")
    evidence = chain.get("evidence", [])
    bank = chain.get("bank", "")
    updated = 0

    # Build entity→column lookup
    entity_map: dict[str, str] = {}
    pattern_map: dict[str, str] = {}
    for col_name, col_cfg in columns.items():
        for ent in col_cfg.get("entities", []):
            entity_map[ent.lower()] = col_name
        for pat in col_cfg.get("patterns", []):
            pattern_map[pat.lower()] = col_name

    for ev in evidence:
        metric = ev.get("metric", "")
        value = ev.get("value", "")
        source = ev.get("source", "")
        combined = f"{metric} {value} {driver}".lower()

        # Skip if the metric ITSELF is about growth rates/CAGR (not if revision note in parens)
        if any(kw in metric.lower() for kw in ("cagr", "增长率", "复合增长", "growth rate")):
            continue
        # Skip if value is purely a percentage (e.g. "80%", "+12%")
        cleaned_val = value.split("（")[0].split("(")[0].strip()
        if not cleaned_val or cleaned_val.endswith("%"):
            continue

        # 1. Period — prefer years with E/F suffix
        period = _parse_period(combined)
        if not period:
            continue
        # Filter: if the year is clearly historical (no E/F/forecast context) AND
        # it appears in a CAGR context, skip
        year_str = period[:4]
        if f"{year_str}-20" in combined or f"{year_str}～20" in combined:
            # "2022-2027年CAGR" — year is a range start, not a data point
            continue

        # 2. Number
        num = _parse_number(value)
        if num is None or num <= 0:
            continue

        # 3. Match column: entity in metric ONLY (most precise) → then metric+driver → then full text
        col = None
        metric_lower = metric.lower()
        metric_driver = (metric + " " + driver).lower()
        # Priority 1: entity appears in the metric field itself
        for ent, col_name in entity_map.items():
            if ent in metric_lower:
                col = col_name
                break
        # Priority 2: entity appears in metric+driver (not just full text)
        if not col:
            for ent, col_name in entity_map.items():
                if ent in metric_driver:
                    col = col_name
                    break
        # Priority 3: pattern match
        if not col:
            for pat, col_name in pattern_map.items():
                if re.search(pat, combined):
                    col = col_name
                    break

        if not col:
            continue

        # 4. Convert units (CoWoS-specific: convert to wpm)
        wpm = _convert_to_wpm(num, value)

        # Sanity check
        if wpm <= 0 or wpm > 2000000:
            continue

        # Round to int for large values (wpm is always int)
        if wpm >= 1:
            wpm = int(wpm)

        # 5. Upsert
        note = f"{driver[:60]}; {bank} {source}"
        try:
            existing = conn.execute(
                f"SELECT id FROM {table} WHERE {key_col} = ?", (period,)
            ).fetchone()
            if existing:
                conn.execute(
                    f"UPDATE {table} SET {col} = ?, source = source || ' + {bank}', updated_at = ? WHERE {key_col} = ?",
                    (wpm, now, period)
                )
            else:
                conn.execute(
                    f"INSERT INTO {table} ({key_col}, {col}, csp_note, source, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (period, wpm, note[:80], f"{bank} auto-extract", now)
                )
            updated += 1
        except Exception as e:
            print(f"    ⚠️  {table}.{col} upsert error: {e}")

    return updated


def cmd_extract(date_str: str = None):
    """Scan latest analysis_logic.json files and extract industry metrics into DB.

    Driven by config.json → tracking.industries[].metrics — no hardcoded rules.
    Called by pipeline after Phase 4 when industry triggers are detected.
    """
    from datetime import date as date_type

    industry_configs = _load_industry_metrics_config()
    if not industry_configs:
        print("  No industry metrics configs found (add 'metrics' key to industries in config.json)")
        return

    report_base = Path.home() / "hermes_reports" / "Investment_Banking_Report"
    if date_str is None:
        date_str = date_type.today().strftime("%Y%m%d")
    target_dir = report_base / date_str

    if not target_dir.is_dir():
        print(f"  No reports directory for {date_str}, skipping metric extraction")
        return

    logic_files = sorted(target_dir.glob("*_analysis_logic.json"))
    if not logic_files:
        print(f"  No logic files in {date_str}, skipping")
        return

    conn = sqlite3.connect(str(DB_PATH))
    now = datetime.now().isoformat()
    extracted = 0
    by_industry: dict[str, int] = {}

    for lf in logic_files:
        try:
            chains = json.loads(lf.read_text(encoding="utf-8"))
        except Exception:
            continue

        for chain in chains:
            if not chain.get("evidence"):
                continue
            slugs = _match_industry(chain, industry_configs)
            for slug in slugs:
                cfg = industry_configs[slug]
                n = _extract_industry(conn, chain, cfg, now)
                extracted += n
                by_industry[slug] = by_industry.get(slug, 0) + n

    conn.commit()
    conn.close()

    if extracted:
        detail = ", ".join(f"{s}={n}" for s, n in by_industry.items())
        print(f"  ✅ Industry metric extraction: {extracted} rows ({detail}) from {len(logic_files)} logic files")
    else:
        print(f"  ℹ️  No industry metrics extracted from {len(logic_files)} logic files")


# ============ CLI ============

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Industry Structured Databases")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("seed", help="Initialize tables + seed reference data")
    show_cmd = sub.add_parser("show", help="Show database content")
    show_cmd.add_argument("table", choices=["hbm", "cowos", "memory"])
    extract_cmd = sub.add_parser("extract", help="Extract metrics from latest logic files")
    extract_cmd.add_argument("date", nargs="?", default=None,
                             help="Date directory (default: today)")

    args = parser.parse_args()

    if args.cmd == "seed":
        cmd_seed()
    elif args.cmd == "show":
        cmd_show(args.table)
    elif args.cmd == "extract":
        cmd_extract(args.date)
    else:
        parser.print_help()
