#!/usr/bin/env python3
"""
Industry Structured Data Extractor — LLM-powered extraction from analysis.md.

Replaces regex-based extraction with LLM calls that read the full analysis.md
and output structured JSON matching the reference quality.

Usage:
  python3 industry_extractor.py cowos 20260518   # single industry
  python3 industry_extractor.py --all 20260518     # all triggered industries
"""
import json
import os
import sys
import argparse
import sqlite3
from pathlib import Path
from datetime import date as date_type, datetime

from llm_client import call_llm, get_model

PROJECT_DIR = Path(__file__).parent
REPORT_BASE = Path.home() / "hermes_reports" / "Investment_Banking_Report"
OUTPUT_DIR = Path.home() / "ClaudeCode" / "dashboard" / "data"
DB_PATH = PROJECT_DIR / "industry_metrics.db"

# ============================================================
# Industry Schema Definitions
# ============================================================

COWOS_SCHEMA = {
    "industry": "CoWoS / Advanced Packaging",
    "output_schema": {
        "_meta": {"source": "string", "file": "string", "compiled_at": "string", "compiled_by": "string"},
        "tsmc_capacity_kwfpm": {"note": "string (unit explanation)", "periods": "dict of quarter→kwfpm"},
        "osat_capacity_kwfpm": {"note": "string", "periods": "dict of quarter→kwfpm"},
        "soic_capacity_kwfpm": {"note": "string", "periods": "dict of quarter→kwfpm"},
        "wmcm_capacity_kwfpm": {"note": "string", "periods": "dict of quarter→kwfpm"},
        "annual_output_million": {"note": "string", "years": "dict of year→million wafers"},
        "cowos_capacity_end_of_year_kwfpm": {"note": "string", "years": "dict of year→kwfpm"},
        "customer_breakdown_kwafer": {
            "note": "string",
            "columns": "list of year strings",
            "customers": "dict of customer_name → {year: kwafers, note: string}"
        },
        "chip_shipments_M_units": {
            "note": "string",
            "columns": "list of year strings",
            "chips": "dict of chip_name → {year: M_units}"
        },
        "technology_milestones": {
            "sections": "dict of topic → {status, timeline, note}"
        },
        "key_conclusions": "dict of key → value"
    }
}

MEMORY_SCHEMA = {
    "industry": "HBM / Memory",
    "output_schema": {
        "_meta": {"source": "string", "file": "string", "compiled_at": "string"},
        "hbm_capacity": {"vendors": "list of {vendor, year, tsv_kwpm, yield_pct, utr_pct, output_mn_gb, output_tb, source}"},
        "hbm_supply_demand": {"rows": "list of {year, demand_mn_gb, supply_mn_gb, sufficiency_pct, source}"},
        "memory_pricing": {"rows": "list of {period, product, price_usd, qoq_pct, yoy_pct, price_type, source}"},
        "key_conclusions": "dict of key → value"
    }
}

INDUSTRY_SCHEMAS = {
    "cowos": COWOS_SCHEMA,
    "memory": MEMORY_SCHEMA,
}


# ============================================================
# LLM Prompt
# ============================================================

EXTRACTION_SYSTEM_PROMPT = """You are a senior semiconductor industry data analyst.

Extract ALL quantitative and structured data from the provided sell-side research report analysis AND raw PDF table data.
Output ONLY valid JSON matching the specified schema. No markdown, no explanation.

Rules:
1. Extract every number you find — capacity, shipments, pricing, allocations, timelines
2. The RAW PDF TABLE DATA section contains the actual numbers from the report's tables — PREFER these over the analysis summary
3. For quarterly data: parse all quarters (1Q23, 2Q23, ..., 4Q28E) from the raw table data
4. For customer breakdown: use the exact wafer allocation numbers from Table 1 in the raw PDF data
5. Use null for unknown/missing values, never make up numbers
6. Include source attribution in _meta
7. All numeric values should be numbers (int/float), not strings
8. For the "columns" arrays: list all year columns present
9. For "periods" in capacity data: use quarter keys like "1Q23", "2Q23", "4Q26E"
10. If raw PDF data contradicts analysis summary, prefer raw PDF data"""


def build_extraction_prompt(industry_slug: str) -> str:
    """Build the extraction prompt for a specific industry."""
    schema = INDUSTRY_SCHEMAS.get(industry_slug)
    if not schema:
        return ""
    schema_json = json.dumps(schema["output_schema"], indent=2, ensure_ascii=False)
    return f"""Extract industry data for: {schema['industry']}

Output must follow this JSON schema structure:
{schema_json}

The analysis.md content is provided below. Extract all quantitative data matching this schema.
"""


# ============================================================
# Main extraction logic
# ============================================================

def extract_industry(slug: str, date_str: str = None) -> dict | None:
    """Run LLM extraction for one industry from today's analysis.md files."""
    if date_str is None:
        date_str = date_type.today().strftime("%Y%m%d")

    target_dir = REPORT_BASE / date_str
    if not target_dir.is_dir():
        print(f"  No reports directory for {date_str}")
        return None

    # Collect all analysis.md files for this industry
    schema = INDUSTRY_SCHEMAS.get(slug)
    if not schema:
        print(f"  Unknown industry: {slug}")
        return None

    # Load target companies from Company↔Industry Matrix
    config_path = PROJECT_DIR / "config.json"
    keywords = []
    target_companies = set()
    matrix_path = PROJECT_DIR / "industry_matrix.json"
    if matrix_path.exists():
        matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
        ind_info = matrix.get("industries", {}).get(slug, {})
        target_companies = set(ind_info.get("companies", []))
        print(f"  Target companies from Matrix: {len(target_companies)}")

    # Load industry keywords from config as fallback
    if config_path.exists():
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
        for ind in cfg.get("tracking", {}).get("industries", []):
            if ind.get("slug") == slug:
                keywords = ind.get("keywords", [])
                break

    # Build company keyword set for matching
    co_keywords = set()
    if target_companies:
        co_map = matrix.get("companies", {})
        for co_name in target_companies:
            co_keywords.add(co_name.lower())
            co_info = co_map.get(co_name, {})
            ticker = co_info.get("ticker", "").split(".")[0].lower()
            if ticker:
                co_keywords.add(ticker)

    # Find relevant analysis.md files by company match first, then keyword fallback
    md_files = sorted(target_dir.glob("*_analysis.md"))
    relevant_md = []
    for mf in md_files:
        content = mf.read_text(encoding="utf-8")
        content_lower = content.lower()

        # Primary: match by target company names
        if co_keywords and any(co_kw in content_lower for co_kw in co_keywords):
            relevant_md.append(mf)
        # Fallback: keyword matching
        elif any(kw.lower() in content_lower for kw in keywords[:20]):
            relevant_md.append(mf)
        elif slug == "cowos" and any(kw in content_lower for kw in
            ["cowos", "advanced packaging", "chip on wafer", "先进封装"]):
            relevant_md.append(mf)

    if not relevant_md:
        print(f"  No relevant analysis.md files for {slug} in {date_str}")
        return None

    print(f"  Found {len(relevant_md)} relevant analysis.md for {slug}")

    # --- Build extraction input: analysis.md (structured summary) + raw PDF text (tables) ---
    combined = ""

    # Phase A: analysis.md - LLM summary with key tables and analysis
    for mf in relevant_md:
        name = mf.stem.replace("_analysis", "")
        content = mf.read_text(encoding="utf-8")
        if len(content) > 8000:
            content = content[:8000] + "\n\n[...truncated...]"
        combined += f"\n\n=== ANALYSIS SUMMARY: {name} ===\n{content}"

    # Phase B: raw PDF text — full pages with table/chart data
    import fitz  # PyMuPDF
    pdf_files = sorted(target_dir.glob("*.pdf"))
    for pf in pdf_files:
        pf_stem = pf.stem
        if not any(pf_stem in mf.stem for mf in relevant_md):
            continue
        try:
            doc = fitz.open(str(pf))
            # Extract per-page text, keep pages that look like they have data tables
            data_pages = []
            for i, page in enumerate(doc):
                text = page.get_text()
                text_lower = text.lower()
                if any(kw in text_lower for kw in ["table", "figure", "kwfpm", "wpm", "wafer",
                    "cowos", "shipment", "allocation", "capacity", "consumption", "breakdown",
                    "1q23", "2q23", "3q23", "4q23", "1q24", "2q24"]):
                    # Include the full page text, not just filtered lines
                    data_pages.append((i + 1, text.strip()[:2000]))
            doc.close()
            if data_pages:
                for page_num, page_text in data_pages[:5]:  # max 5 pages per PDF
                    combined += f"\n\n=== PDF {pf_stem[:50]} PAGE {page_num} ===\n{page_text}"
        except Exception as e:
            print(f"    ⚠️  Could not read PDF {pf.name}: {e}")

    if len(combined) > 38000:
        combined = combined[:38000] + "\n\n[...truncated to fit context...]"
    print(f"  Combined input: {len(combined)} chars (analysis + raw PDF page data)")

    # Build prompt and call LLM
    system = EXTRACTION_SYSTEM_PROMPT
    user = build_extraction_prompt(slug) + f"\n\n=== ANALYSIS REPORTS ===\n{combined}"

    print(f"  Calling LLM ({get_model()}) for {slug} extraction ({len(combined)} chars input)...")
    try:
        text, usage = call_llm(system, user, max_tokens=16384, thinking=True)
        print(f"  LLM tokens: {usage['input_tokens']} in, {usage['output_tokens']} out")

        # Parse JSON from LLM response
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1]) if len(lines) > 2 else text

        data = json.loads(text)
        return data
    except json.JSONDecodeError as e:
        print(f"  ❌ JSON parse error: {e}")
        print(f"  Raw response (first 500 chars): {text[:500]}")
        return None
    except Exception as e:
        print(f"  ❌ LLM error: {e}")
        return None


# ============================================================
# Vision-based Chart Figure Extraction
# ============================================================

CHART_EXTRACTION_PROMPT = """Extract structured numerical data from this chart/table image from a semiconductor research report.

For each figure/table you see, extract ALL numerical data points:

1. If it's a CHARTS with multiple data series (e.g. CoWoS capacity over time):
   - Identify what each colored bar/line represents
   - Read the Y-axis values for each data point
   - Read the X-axis labels (quarters: 1Q23, 2Q23, ..., 4Q28E)
   - Output as an array of {period: value} pairs

2. If it's a TABLE with customer allocations:
   - Read every row and column
   - Output customer name → {year: value} mapping

3. If there are multiple sub-charts, label each one

Output format (JSON only, no markdown):
{
  "figures": [
    {
      "title": "Figure 2: TSMC CoWoS capacity",
      "type": "chart",
      "unit": "kwfpm (thousands of wafers per month)",
      "data": {
        "TSMC_CoWoS": {"1Q23": 15, "2Q23": 19, ...},
        "OSAT": {"1Q23": 1, "2Q23": 4, ...}
      }
    },
    {
      "title": "Table 1: CoWoS allocation by customer",
      "type": "table",
      "unit": "thousands of wafers per year",
      "data": {
        "NVIDIA": {"2023": null, "2024": null, "2025": null, "2026E": 720, "2027E": 1120},
        "AMD": {"2023": null, "2024": null, "2025": null, "2026E": 80, "2027E": 180},
        ...
      }
    }
  ]
}

Be precise with numbers. If a value is unclear, use null. Don't guess."""


def _extract_chart_data_vision(slug: str, target_dir: Path, relevant_md: list) -> dict | None:
    """Render key PDF pages as images and extract structured data via vision LLM."""
    if slug not in ("cowos",):  # currently only CoWoS has chart-heavy data
        return None

    import fitz
    import base64
    import io
    from PIL import Image

    pdf_files = sorted(target_dir.glob("*.pdf"))
    images_sent = 0
    all_chart_data = []

    for pf in pdf_files:
        pf_stem = pf.stem
        if not any(pf_stem in mf.stem for mf in relevant_md):
            continue

        try:
            doc = fitz.open(str(pf))
            chart_pages = []
            for i, page in enumerate(doc):
                text = page.get_text().lower()
                # Pages likely to contain charts/tables
                if any(kw in text for kw in ["figure", "table", "kwfpm", "shipment",
                    "cowos capacity", "allocation", "consumption", "1q23", "1q24"]):
                    chart_pages.append(i)
                # Also heuristic: pages with few text but part of the report body
                elif len(text) < 300 and i > 2 and i < doc.page_count - 2:
                    chart_pages.append(i)

            if not chart_pages:
                doc.close()
                continue

            # Limit to 4 most data-rich pages
            chart_pages = chart_pages[:4]
            print(f"  📸 Rendering {len(chart_pages)} chart pages from {pf.name}...")

            for page_idx in chart_pages:
                page = doc[page_idx]
                # Render at 150 DPI (balance quality vs size)
                mat = fitz.Matrix(150/72, 150/72)
                pix = page.get_pixmap(matrix=mat)
                img_bytes = pix.tobytes("png")

                # Resize if too large (>2MB after base64)
                if len(img_bytes) > 1.5 * 1024 * 1024:
                    pil_img = Image.open(io.BytesIO(img_bytes))
                    pil_img.thumbnail((1200, 1600))
                    buf = io.BytesIO()
                    pil_img.save(buf, format="PNG", optimize=True)
                    img_bytes = buf.getvalue()

                img_b64 = base64.b64encode(img_bytes).decode()
                images_sent += 1

                # Send to vision LLM
                try:
                    from llm_client import get_client, get_model
                    client = get_client()
                    response = client.messages.create(
                        model=get_model(),
                        system=CHART_EXTRACTION_PROMPT,
                        messages=[{
                            "role": "user",
                            "content": [
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "image/png",
                                        "data": img_b64,
                                    }
                                },
                                {
                                    "type": "text",
                                    "text": f"Extract all data from page {page_idx + 1} (figures and tables). Output JSON only."
                                }
                            ]
                        }],
                        max_tokens=4096,
                    )
                    text = "\n".join(b.text for b in response.content if b.type == "text")
                    # Parse JSON
                    text = text.strip()
                    if text.startswith("```"):
                        lines = text.split("\n")
                        text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
                    chart_json = json.loads(text)
                    chart_json["_page"] = page_idx + 1
                    chart_json["_pdf"] = pf.name
                    all_chart_data.append(chart_json)
                except json.JSONDecodeError:
                    print(f"    ⚠️  Page {page_idx+1}: JSON parse error, skipping")
                except Exception as e:
                    print(f"    ⚠️  Page {page_idx+1}: {e}")

            doc.close()
        except Exception as e:
            print(f"    ⚠️  Chart extraction error for {pf.name}: {e}")

    if images_sent > 0:
        print(f"  📊 Vision extraction: {images_sent} chart images, {len(all_chart_data)} parsed")
    return {"charts": all_chart_data} if all_chart_data else None


def _merge_chart_data(text_data: dict, chart_data: dict) -> dict:
    """Merge vision-extracted chart data into the text extraction.
    Chart data (directly read from figures) takes priority over text-derived numbers."""
    charts = chart_data.get("charts", [])
    if not charts:
        return text_data

    for chart in charts:
        figures = chart.get("figures", [])
        for fig in figures:
            title = fig.get("title", "").lower()
            fig_data = fig.get("data", {})

            # Figure 2: TSMC CoWoS capacity → quarterly periods
            if "cowos" in title and "capacity" in title and "table" not in fig.get("type", ""):
                for series_name, series_data in fig_data.items():
                    if not isinstance(series_data, dict):
                        continue
                    sl = series_name.lower()
                    if "tsmc" in sl or "cowos" in sl:
                        text_data.setdefault("tsmc_capacity_kwfpm", {})
                        text_data["tsmc_capacity_kwfpm"]["periods"] = series_data
                    elif "osat" in sl or "ase" in sl or "amkor" in sl:
                        text_data.setdefault("osat_capacity_kwfpm", {})
                        text_data["osat_capacity_kwfpm"]["periods"] = series_data

            # Figure 3/4: SoIC / WMCM
            elif "soic" in title:
                for series_name, series_data in fig_data.items():
                    if isinstance(series_data, dict):
                        text_data.setdefault("soic_capacity_kwfpm", {})
                        text_data["soic_capacity_kwfpm"]["periods"] = series_data
            elif "wmcm" in title or "cow-r" in title:
                for series_name, series_data in fig_data.items():
                    if isinstance(series_data, dict):
                        text_data.setdefault("wmcm_capacity_kwfpm", {})
                        text_data["wmcm_capacity_kwfpm"]["periods"] = series_data

            # Table 1: Customer allocation
            elif "table" in fig.get("type", "") or "allocation" in title or "customer" in title:
                if fig_data:
                    text_data.setdefault("customer_breakdown_kwafer", {})
                    existing = text_data["customer_breakdown_kwafer"].get("customers", {})
                    for cust_name, cust_data in fig_data.items():
                        if isinstance(cust_data, dict):
                            existing[cust_name] = cust_data
                    text_data["customer_breakdown_kwafer"]["customers"] = existing

            # Chip shipments
            elif "shipment" in title or "unit" in title:
                if fig_data:
                    text_data.setdefault("chip_shipments_M_units", {})
                    text_data["chip_shipments_M_units"]["chips"] = fig_data

    return text_data


def save_and_upsert(slug: str, data: dict, date_str: str):
    """Save structured JSON to file and upsert key metrics to DB."""
    # Save JSON
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{slug}_capacity.json"
    data["_meta"]["compiled_at"] = date_str
    data["_meta"]["compiled_by"] = "Hermes Pipeline (LLM)"
    out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  ✅ Saved: {out_path}")

    # Upsert to DB — key tables
    conn = sqlite3.connect(str(DB_PATH))
    now = datetime.now().isoformat()

    if slug == "cowos":
        _upsert_cowos_db(conn, data, now)
    elif slug == "memory":
        _upsert_memory_db(conn, data, now)

    conn.commit()
    conn.close()


def _upsert_cowos_db(conn, data: dict, now: str):
    """Upsert CoWoS data to industry_metrics.db."""
    # Quarterly capacity → cowos_capacity table (new quarterly rows)
    tsmc_q = data.get("tsmc_capacity_kwfpm", {})
    osat_q = data.get("osat_capacity_kwfpm", {})
    soic_q = data.get("soic_capacity_kwfpm", {})
    wmcm_q = data.get("wmcm_capacity_kwfpm", {})

    for period, val in tsmc_q.items():
        if not isinstance(val, (int, float)) or val <= 0:
            continue
        if period in ("note",):
            continue
        # kwfpm → wpm (always ×1000)
        wpm = int(val * 1000)
        conn.execute(
            """INSERT OR REPLACE INTO cowos_capacity (period, total_wpm, csp_note, source, updated_at)
               VALUES (?, ?, 'TSMC CoWoS quarterly', 'LLM-extract', ?)""",
            (period, wpm, now)
        )
        # Also add OSAT if present
        osat_val = osat_q.get(period)
        if isinstance(osat_val, (int, float)) and osat_val > 0:
            osat_wpm = int(osat_val * 1000)
            conn.execute(
                """UPDATE cowos_capacity SET other_wpm = ?, csp_note = csp_note || ' | OSAT:' || ?
                   WHERE period = ?""",
                (osat_wpm, str(osat_val), period)
            )

    # Customer allocation → annual wpm conversion
    cust_data = data.get("customer_breakdown_kwafer", {})
    cust_col_map = {
        "nvidia": "nvidia_wpm", "amd": "amd_wpm", "broadcom": "broadcom_wpm",
        "mediatek": "mediatek_wpm", "intel": "intel_wpm",
        "alchip": "other_wpm", "marvell": "other_wpm", "google": "google_wpm",
    }
    for cust_name, cust_info in cust_data.get("customers", {}).items():
        for year, kwafers in cust_info.items():
            if not isinstance(kwafers, (int, float)) or kwafers <= 0:
                continue
            if year in ("note", "columns"):
                continue
            # kwafers = thousands of wafers per year → wpm = *1000/12
            wpm = int(kwafers * 1000 / 12)
            col = None
            cust_key = cust_name.lower().replace(" ", "_")
            for key, col_name in cust_col_map.items():
                if key in cust_key:
                    col = col_name
                    break
            if col:
                # Normalize period: strip existing E suffix, re-add one
                period = year.rstrip("EeFf") + "E"
                existing = conn.execute(
                    "SELECT id FROM cowos_capacity WHERE period = ?", (period,)
                ).fetchone()
                if existing:
                    conn.execute(
                        f"UPDATE cowos_capacity SET {col} = ?, source = source || ' + LLM' WHERE period = ?",
                        (wpm, period)
                    )
                else:
                    conn.execute(
                        f"INSERT INTO cowos_capacity (period, {col}, source, updated_at) VALUES (?, ?, 'LLM-extract', ?)",
                        (period, wpm, now)
                    )

    print(f"  ✅ DB upserted: cowos_capacity")


def _upsert_memory_db(conn, data: dict, now: str):
    """Upsert Memory/HBM data to industry_metrics.db."""
    hbm = data.get("hbm_capacity", {})
    for v in hbm.get("vendors", []):
        conn.execute(
            """INSERT OR REPLACE INTO hbm_capacity
               (vendor, year, tsv_kwpm, yield_pct, utr_pct, hbm_output_mn_gb, hbm_output_tb, source, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (v.get("vendor"), v.get("year"), v.get("tsv_kwpm"), v.get("yield_pct"),
             v.get("utr_pct"), v.get("output_mn_gb"), v.get("output_tb"),
             v.get("source", "LLM-extract"), now)
        )
    print(f"  ✅ DB upserted: hbm_capacity")


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LLM-powered Industry Data Extractor")
    parser.add_argument("slug", nargs="?", help="Industry slug (cowos, memory)")
    parser.add_argument("date", nargs="?", default=None, help="Date dir (default: today)")
    parser.add_argument("--all", action="store_true", help="Run all configured industries")
    args = parser.parse_args()

    if args.all:
        slugs = list(INDUSTRY_SCHEMAS.keys())
    elif args.slug:
        slugs = [args.slug]
    else:
        parser.print_help()
        sys.exit(1)

    for slug in slugs:
        print(f"\n📊 Extracting: {slug}")
        data = extract_industry(slug, args.date)
        if data:
            save_and_upsert(slug, data, args.date or date_type.today().strftime("%Y%m%d"))
        else:
            print(f"  ⚠️  No data extracted for {slug}")
