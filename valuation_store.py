#!/usr/bin/env python3
"""Valuation data SQLite storage — persist and query valuation metrics."""

import json
import re
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Optional

PROJECT_DIR = Path(__file__).parent
DB_PATH = PROJECT_DIR / "valuation.db"
REPORT_BASE = Path.home() / "hermes_reports" / "Investment_Banking_Report"


class ValuationStore:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS valuations (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                report_path     TEXT NOT NULL UNIQUE,
                pdf_name        TEXT NOT NULL,
                company         TEXT NOT NULL,
                bank            TEXT NOT NULL,
                report_date     TEXT,
                rating          TEXT,
                tp_new          REAL,
                tp_old          REAL,
                tp_currency     TEXT DEFAULT 'USD',
                eps_forecast    TEXT,
                pe_current      REAL,
                pe_historical   REAL,
                valuation_method TEXT,
                eps_quality     INTEGER DEFAULT 0,
                created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_val_company ON valuations(company);
            CREATE INDEX IF NOT EXISTS idx_val_bank ON valuations(bank);

            CREATE TABLE IF NOT EXISTS consensus_cache (
                company         TEXT NOT NULL,
                fiscal_year     TEXT,
                metric          TEXT NOT NULL,
                median_val      REAL,
                mean_val        REAL,
                std_dev         REAL,
                count           INTEGER,
                min_val         REAL,
                max_val         REAL,
                generated_at    TEXT NOT NULL,
                UNIQUE(company, fiscal_year, metric)
            );

            CREATE INDEX IF NOT EXISTS idx_cc_company ON consensus_cache(company);
        """)
        self.conn.commit()

    # ---- Write ----

    def upsert_from_analysis(self, json_path: str) -> Optional[int]:
        """从 _analysis.json 提取估值数据并写入。返回 valuation_id 或 None。"""
        jp = Path(json_path)
        if not jp.exists():
            return None

        try:
            data = json.loads(jp.read_text(encoding="utf-8"))
        except Exception:
            return None

        parsed = data.get("parsed", {})
        if not parsed:
            return None

        pdf_name = data.get("pdf_name", jp.stem.replace("_analysis", ""))
        raw_company = parsed.get("company", "") or ""
        if not raw_company:
            return None
        # Normalize company name for consistent querying
        from run_pipeline import normalize_company
        company = normalize_company(raw_company)

        # Extract bank from filename
        bank = ""
        m = re.match(r'^([A-Za-z\s&.]+?)[-（(]', pdf_name)
        if m:
            bank = m.group(1).strip()

        # Extract date from filename suffix -YYMMDD
        report_date = ""
        dm = re.search(r'(\d{6})(?:\.pdf|\.pptx|\.xlsx|$)', pdf_name)
        if dm:
            ds = dm.group(1)
            report_date = f"20{ds[:2]}-{ds[2:4]}-{ds[4:6]}"

        tp = parsed.get("target_price") or {}
        pe = parsed.get("pe_multiple") or {}

        # EPS quality: 0=unknown, 1=low, 2=medium, 3=high
        eps = parsed.get("eps_forecast") or {}
        eps_quality = self._score_eps_quality(eps, pe.get("current"), tp.get("new"))

        self.conn.execute(
            """INSERT OR REPLACE INTO valuations
               (report_path, pdf_name, company, bank, report_date, rating,
                tp_new, tp_old, tp_currency, eps_forecast,
                pe_current, pe_historical, valuation_method, eps_quality, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (str(jp), pdf_name, company, bank, report_date,
             parsed.get("rating", ""),
             tp.get("new"), tp.get("old"), tp.get("currency", ""),
             json.dumps(eps, ensure_ascii=False) if eps else None,
             pe.get("current"), pe.get("historical"),
             parsed.get("valuation_method", ""),
             eps_quality,
             datetime.now().isoformat())
        )
        self.conn.commit()

        # Invalidate consensus cache for this company
        self.conn.execute("DELETE FROM consensus_cache WHERE company=?", (company,))
        self.conn.commit()

        return self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def _score_eps_quality(self, eps: dict, pe_current, tp_new) -> int:
        """Score EPS data quality 0-3."""
        if not eps:
            return 0
        score = 1
        if len(eps) >= 3:
            score = 2
        # Cross-validate: PE * EPS should be within 50%-200% of TP
        if pe_current and tp_new and eps:
            latest_eps = list(eps.values())[-1]
            if latest_eps > 0 and pe_current > 0:
                implied = latest_eps * pe_current
                if 0.5 < implied / tp_new < 2.0:
                    score = max(score, 3)
        return score

    # ---- Read ----

    def _company_candidates(self, company: str) -> list[str]:
        """Return exact DB company candidates for a user-supplied name/ticker."""
        raw = (company or "").strip()
        candidates = []
        if raw:
            candidates.append(raw)

        try:
            from run_pipeline import normalize_company
            normalized = normalize_company(raw)
            if normalized and normalized != "Unknown":
                candidates.append(normalized)
        except Exception:
            pass

        # Preserve DB casing for case-insensitive exact matches.
        for name in list(candidates):
            rows = self.conn.execute(
                "SELECT DISTINCT company FROM valuations WHERE lower(company)=lower(?)",
                (name,)
            ).fetchall()
            candidates.extend(r["company"] for r in rows)

        seen = set()
        result = []
        for c in candidates:
            key = c.lower()
            if c and key not in seen:
                seen.add(key)
                result.append(c)
        return result

    def _rows_to_reports(self, rows) -> list[dict]:
        result = []
        for r in rows:
            d = dict(r)
            d["eps_forecast"] = json.loads(d["eps_forecast"]) if d["eps_forecast"] else {}
            # Map to the keys compute_consensus expects
            d["pe"] = d.get("pe_current")
            d["tp_new"] = d.get("tp_new")
            d["tp_currency"] = d.get("tp_currency")
            d["rating"] = d.get("rating")
            d["method"] = d.get("valuation_method")
            result.append(d)
        return result

    def get_by_company(self, company: str, months: int = 6) -> list[dict]:
        """获取一家公司的估值记录。

        Uses canonical company aliases and keeps undated rows. Some legacy imports
        have TP data but no parsed report_date; excluding them makes the UI show
        "no data" even though valuation.db has broker target prices.
        """
        candidates = self._company_candidates(company)
        if not candidates:
            return []

        placeholders = ",".join("?" * len(candidates))
        order_by = """ORDER BY
            CASE WHEN report_date IS NULL OR report_date='' THEN 1 ELSE 0 END,
            report_date DESC, updated_at DESC"""

        if months is None:
            rows = self.conn.execute(
                f"SELECT * FROM valuations WHERE company IN ({placeholders}) {order_by}",
                candidates
            ).fetchall()
            return self._rows_to_reports(rows)

        rows = self.conn.execute(
            f"""SELECT * FROM valuations
                WHERE company IN ({placeholders})
                  AND (report_date >= date('now', ?)
                       OR report_date IS NULL OR report_date='')
                {order_by}""",
            [*candidates, f'-{months} months']
        ).fetchall()

        # If all reports are outside the default window, return historical data
        # instead of hiding an existing TP-bearing company.
        if not rows:
            rows = self.conn.execute(
                f"SELECT * FROM valuations WHERE company IN ({placeholders}) {order_by}",
                candidates
            ).fetchall()
        return self._rows_to_reports(rows)

    def get_all_companies(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT company FROM valuations ORDER BY company"
        ).fetchall()
        return [r[0] for r in rows]

    def get_peers(self, peer_names: list[str]) -> list[dict]:
        """获取同行公司的估值摘要。"""
        if not peer_names:
            return []
        placeholders = ",".join("?" * len(peer_names))
        rows = self.conn.execute(
            f"""SELECT company, pe_current, tp_new, tp_currency, eps_forecast
                FROM valuations
                WHERE company IN ({placeholders})
                AND pe_current IS NOT NULL
                ORDER BY report_date DESC""",
            peer_names
        ).fetchall()
        result = []
        seen = set()
        for r in rows:
            if r["company"] in seen:
                continue
            seen.add(r["company"])
            eps = json.loads(r["eps_forecast"]) if r["eps_forecast"] else {}
            latest_eps = list(eps.values())[-1] if eps else None
            result.append({
                "name": r["company"],
                "pe": r["pe_current"],
                "tp": r["tp_new"],
                "tp_currency": r["tp_currency"],
                "eps": latest_eps,
            })
        return result

    def get_consensus(self, company: str) -> dict:
        """读取缓存的共识数据。"""
        rows = self.conn.execute(
            "SELECT metric, fiscal_year, median_val, count, min_val, max_val FROM consensus_cache WHERE company=?",
            (company,)
        ).fetchall()
        result = {"company": company}
        for r in rows:
            result[f"{r['metric']}_{r['fiscal_year'] or 'all'}"] = {
                "median": r["median_val"], "count": r["count"],
                "min": r["min_val"], "max": r["max_val"],
            }
        return result

    def save_consensus(self, company: str, metric: str, fiscal_year: str,
                       values: list[float]) -> None:
        """保存共识计算结果到缓存。"""
        if not values:
            return
        sv = sorted(values)
        n = len(sv)
        mean_val = sum(sv) / n
        median_val = sv[n // 2]
        std_dev = (sum((v - mean_val) ** 2 for v in sv) / n) ** 0.5

        self.conn.execute(
            """INSERT OR REPLACE INTO consensus_cache
               (company, fiscal_year, metric, median_val, mean_val, std_dev, count, min_val, max_val, generated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (company, fiscal_year, metric, median_val, mean_val,
             round(std_dev, 4), n, sv[0], sv[-1], datetime.now().isoformat())
        )
        self.conn.commit()

    def backfill(self) -> int:
        """回填所有已有报告。"""
        processed = 0
        for jf in sorted(REPORT_BASE.rglob("*_analysis.json")):
            if self.upsert_from_analysis(str(jf)):
                processed += 1
        return processed

    def close(self):
        self.conn.close()
