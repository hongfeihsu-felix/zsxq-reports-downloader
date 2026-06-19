#!/usr/bin/env python3
"""
Logic chain SQLite storage — persist and query causal reasoning chains.

Tables:
  logic_chains       — 每份报告的每条逻辑链
  evidence_points    — 1:N to logic_chain
  impacts            — 1:N to logic_chain
  aggregated_drivers — Phase 3.5 聚合结果缓存
"""

import json
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Optional

from logic_schema import LogicChain, AggregatedDriver

DB_PATH = Path(__file__).parent / "logic_chains.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS logic_chains (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_path TEXT NOT NULL,
            company TEXT NOT NULL,
            bank TEXT NOT NULL,
            date TEXT NOT NULL,
            driver_slug TEXT NOT NULL,
            driver_raw TEXT NOT NULL,
            direction TEXT NOT NULL,
            confidence TEXT NOT NULL DEFAULT 'medium',
            change_from_prior TEXT DEFAULT '',
            prior_reference TEXT DEFAULT '',
            ticker TEXT DEFAULT '',
            raw_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS evidence_points (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chain_id INTEGER NOT NULL REFERENCES logic_chains(id) ON DELETE CASCADE,
            metric TEXT NOT NULL,
            value TEXT NOT NULL,
            source TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS impacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chain_id INTEGER NOT NULL REFERENCES logic_chains(id) ON DELETE CASCADE,
            entity TEXT NOT NULL,
            role TEXT NOT NULL,
            effect TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS aggregated_drivers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company TEXT NOT NULL,
            driver_slug TEXT NOT NULL,
            consensus_level TEXT NOT NULL,
            bank_count INTEGER NOT NULL DEFAULT 0,
            aggregated_json TEXT NOT NULL,
            generated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(company, driver_slug)
        );

        CREATE INDEX IF NOT EXISTS idx_lc_company ON logic_chains(company);
        CREATE INDEX IF NOT EXISTS idx_lc_driver ON logic_chains(driver_slug);
        CREATE INDEX IF NOT EXISTS idx_lc_date ON logic_chains(date);
        CREATE INDEX IF NOT EXISTS idx_impacts_entity ON impacts(entity);
        CREATE INDEX IF NOT EXISTS idx_evidence_chain ON evidence_points(chain_id);
        CREATE INDEX IF NOT EXISTS idx_impacts_chain ON impacts(chain_id);
    """)
    conn.commit()
    conn.close()


def _slugify(text: str) -> str:
    """Normalize driver text to a URL-safe slug."""
    import re
    slug = text.lower().strip()
    slug = re.sub(r'[^\w\s-]', '', slug)
    slug = re.sub(r'[\s_]+', '_', slug)
    return slug[:80]


def save_logic_chain(chain: LogicChain, report_path: str):
    """Persist a single logic chain + its evidence/impacts."""
    conn = get_conn()
    driver_slug = _slugify(chain.driver)

    cursor = conn.execute(
        """INSERT INTO logic_chains
           (report_path, company, bank, date, driver_slug, driver_raw,
            direction, confidence, change_from_prior, prior_reference, ticker, raw_json)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (report_path, chain.company, chain.bank, chain.date,
         driver_slug, chain.driver, chain.direction, chain.confidence,
         chain.change_from_prior, chain.prior_reference, chain.ticker,
         json.dumps(chain.to_dict(), ensure_ascii=False))
    )
    chain_id = cursor.lastrowid

    for ev in chain.evidence:
        conn.execute(
            "INSERT INTO evidence_points (chain_id, metric, value, source) VALUES (?,?,?,?)",
            (chain_id, ev["metric"], ev["value"], ev["source"])
        )
    for imp in chain.impacts:
        conn.execute(
            "INSERT INTO impacts (chain_id, entity, role, effect) VALUES (?,?,?,?)",
            (chain_id, imp["entity"], imp["role"], imp["effect"])
        )

    conn.commit()
    conn.close()
    return chain_id


def save_logic_chains(chains: list[LogicChain], report_path: str) -> list[int]:
    """Persist multiple logic chains for one report."""
    ids = []
    for chain in chains:
        chain_id = save_logic_chain(chain, report_path)
        ids.append(chain_id)
    return ids


def query_by_company(company: str, days: int = 30) -> list[dict]:
    """Query all logic chains for a company within N days."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT * FROM logic_chains
           WHERE company = ? AND date(date) >= date('now', ?)
           ORDER BY date DESC""",
        (company, f"-{days} days")
    ).fetchall()
    conn.close()
    return [_row_to_chain_dict(r) for r in rows]


def query_by_driver(driver_slug: str, days: int = 90) -> list[dict]:
    """Query all logic chains for a given driver slug."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT * FROM logic_chains
           WHERE driver_slug = ? AND date(date) >= date('now', ?)
           ORDER BY date DESC""",
        (driver_slug, f"-{days} days")
    ).fetchall()
    conn.close()
    return [_row_to_chain_dict(r) for r in rows]


def query_by_entity(entity: str, days: int = 90) -> list[dict]:
    """Query all logic chains that impact a given entity (upstream/downstream)."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT DISTINCT lc.* FROM logic_chains lc
           JOIN impacts im ON im.chain_id = lc.id
           WHERE im.entity = ? AND date(lc.date) >= date('now', ?)
           ORDER BY lc.date DESC""",
        (entity, f"-{days} days")
    ).fetchall()
    conn.close()
    return [_row_to_chain_dict(r) for r in rows]


def getAllLogicChainsForCompany(company: str) -> list[dict]:
    """Get ALL logic chains for a company (no date filter)."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT * FROM logic_chains
           WHERE company = ?
           ORDER BY date DESC""",
        (company,)
    ).fetchall()
    conn.close()
    return [_row_to_chain_dict(r) for r in rows]


def save_aggregated_driver(ad: AggregatedDriver):
    """Upsert an aggregated driver result."""
    conn = get_conn()
    conn.execute(
        """INSERT OR REPLACE INTO aggregated_drivers
           (company, driver_slug, consensus_level, bank_count, aggregated_json, generated_at)
           VALUES (?,?,?,?,?,datetime('now'))""",
        (ad.company, ad.slug, ad.consensus_level, len(ad.banks),
         json.dumps(ad.to_dict(), ensure_ascii=False))
    )
    conn.commit()
    conn.close()


def load_aggregated_drivers(company: str) -> list[dict]:
    """Load cached aggregated drivers for a company."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT aggregated_json FROM aggregated_drivers
           WHERE company = ? ORDER BY bank_count DESC""",
        (company,)
    ).fetchall()
    conn.close()
    return [json.loads(r[0]) for r in rows]


def _row_to_chain_dict(row) -> dict:
    """Convert a sqlite3.Row to a dict with evidence and impacts."""
    d = dict(row)
    # Normalize field names for compatibility
    if "driver_raw" in d and "driver" not in d:
        d["driver"] = d["driver_raw"]
    d["evidence"] = _load_evidence(d["id"])
    d["impacts"] = _load_impacts(d["id"])
    return d


def _load_evidence(chain_id: int) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT metric, value, source FROM evidence_points WHERE chain_id = ?",
        (chain_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _load_impacts(chain_id: int) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT entity, role, effect FROM impacts WHERE chain_id = ?",
        (chain_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# Auto-init on import
if not DB_PATH.exists():
    init_db()
