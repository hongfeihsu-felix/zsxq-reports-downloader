#!/usr/bin/env python3
"""
Macro event risk calendar — FOMC, CPI, employment, mega IPO windows.
Integrated into signal_scorer to adjust scores during high-risk windows.

Usage:
  from macro_events import get_event_risk
  risk = get_event_risk()  # returns {score, warnings, details}
"""

import sqlite3
from pathlib import Path
from datetime import datetime, date, timedelta

PROJECT_DIR = Path(__file__).parent

# ── Hardcoded 2026 Macro Calendar ──────────────────────

# FOMC meetings 2026 (2-day meetings, decision day listed)
# Nasdaq-100 index rebalance (SPCX inclusion)
NAS100_REBALANCE = {
    "2026-06-27",  # SPCX enters Nasdaq-100 (15 days post-IPO)
}

# FOMC meetings 2026 (2-day meetings, decision day listed)
FOMC_DATES = {
    "2026-01-29", "2026-03-19", "2026-05-07", "2026-06-18",
    "2026-07-30", "2026-09-17", "2026-11-05", "2026-12-17",
}

# CPI release (monthly, ~10th-14th)
CPI_DATES = {
    "2026-01-14", "2026-02-12", "2026-03-12", "2026-04-10",
    "2026-05-13", "2026-06-11", "2026-07-14", "2026-08-12",
    "2026-09-11", "2026-10-14", "2026-11-12", "2026-12-11",
}

# Non-farm payrolls (monthly, first Friday)
NFP_DATES = {
    "2026-01-09", "2026-02-06", "2026-03-06", "2026-04-03",
    "2026-05-08", "2026-06-05", "2026-07-03", "2026-08-07",
    "2026-09-04", "2026-10-02", "2026-11-06", "2026-12-04",
}

# Major known IPOs / lockup expiries (approximate windows)
MEGA_IPO_WINDOWS = {
    # Format: (start_date, end_date, name, description)
    ("2026-06-24", "2026-07-12", "SPCX Post-IPO/Lockup", "SpaceX $75B IPO lockup expiry late July; Nasdaq-100 inclusion, passive rebalance flows"),
    # CoreWeave (CRWV) already IPO'd March 2025 — removed
    ("2026-07-01", "2026-07-31", "OpenAI IPO", "ChatGPT maker, rumored $60B raise, $400B+ valuation — AI liquidity drain"),
    ("2026-08-01", "2026-08-31", "SK Hynix ADR", "HBM memory leader, rumored $12B raise, $200B valuation — AI memory play"),
    ("2026-09-01", "2026-09-15", "Databricks IPO", "Data + AI platform, expected $5B raise, $50B valuation"),
    ("2026-09-01", "2026-09-30", "Anthropic IPO", "Claude maker, rumored $60B raise, $400B+ valuation"),
}


# ── Thematic / Ongoing Risks ─────────────────────────

# CSP CapEx spiral: Google/MSFT/Amazon/Oracle spending B+/yr on AI infra
# → FCF erosion → debt/equity issuance → market liquidity drain
# Flag: active when any tracked CSP has bearish signals about CapEx/cash flow
CSP_CAPEX_RISK = True  # 2026 H1-H2 ongoing

# Tier-1 bank risk scanner
TIER1_BANKS = ["Goldman Sachs", "J.P. Morgan", "Morgan Stanley", "Bernstein"]

def _count_tier1_high_bearish(days: int = 7) -> int:
    """Count recent high-confidence bearish signals from tier-1 banks."""
    try:
        import sqlite3
        conn = sqlite3.connect(str(PROJECT_DIR / "logic_chains.db"))
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        placeholders = ",".join("?" * len(TIER1_BANKS))
        cnt = conn.execute(
            f"SELECT COUNT(*) FROM logic_chains WHERE direction='bearish' AND confidence='high' AND bank IN ({placeholders}) AND date >= ?",
            TIER1_BANKS + [cutoff]
        ).fetchone()[0]
        conn.close()
        return cnt
    except Exception:
        return 0

# ── Event Risk Scoring ─────────────────────────────────

def get_event_risk(today: date = None) -> dict:
    """Calculate event risk score for today and the next few days.

    Returns:
        {score: float,      # 0.0=normal, 0.5=elevated, 1.0=high
         warnings: [str],   # human-readable warnings
         events: [dict]}     # detailed event list
    """
    if today is None:
        today = date.today()

    score = 0.0
    warnings = []
    events = []

    today_str = today.strftime("%Y-%m-%d")
    window_start = today - timedelta(days=1)
    window_end = today + timedelta(days=2)

    # 1) FOMC
    for d in FOMC_DATES:
        if window_start <= date.fromisoformat(d) <= window_end:
            score += 0.3
            warnings.append(f"FOMC rate decision {d}")
            events.append({"type": "FOMC", "date": d, "severity": "high"})

    # 2) CPI
    for d in CPI_DATES:
        if window_start <= date.fromisoformat(d) <= window_end:
            score += 0.2
            warnings.append(f"CPI data release {d}")
            events.append({"type": "CPI", "date": d, "severity": "medium"})

    # 3) NFP
    for d in NFP_DATES:
        if window_start <= date.fromisoformat(d) <= window_end:
            score += 0.2
            warnings.append(f"NFP employment report {d}")
            events.append({"type": "NFP", "date": d, "severity": "medium"})

    # 3.5) Nasdaq rebalance (SPCX inclusion)
    for d in NAS100_REBALANCE:
        s = date.fromisoformat(d)
        if today <= s <= today + timedelta(days=5):
            score += 0.2
            warnings.append(f"Nasdaq-100 rebalance: SPCX inclusion {d} — passive fund forced selling")
            events.append({"type": "NAS100_REBALANCE", "date": d, "severity": "medium"})

    # 4) Mega IPO windows
    for start, end, name, desc in MEGA_IPO_WINDOWS:
        s = date.fromisoformat(start)
        e = date.fromisoformat(end)
        if s <= today <= e:
            weight = 0.25 if "SpaceX" in name else 0.15  # SpaceX is ~2x Saudi Aramco
            score += weight
            sev = "high" if weight > 0.2 else "low"
            warnings.append(f"{name}: {desc}")
            events.append({"type": "MEGA_IPO", "name": name, "severity": sev})
        elif today <= s <= today + timedelta(days=7):
            # IPO approaching — early warning
            warnings.append(f"Upcoming: {name} ({s}) — may drain liquidity")
            events.append({"type": "MEGA_IPO_UPCOMING", "name": name, "date": start, "severity": "low"})

    # 4.3) Tier-1 bank bearish signals (systemic risk barometer)
    tier1_count = _count_tier1_high_bearish(days=7)
    if tier1_count >= 100:
        score += 0.2
        warnings.append(f"Tier-1 banks (GS/JPM/MS/Bernstein): {tier1_count} high-conf bearish signals in 7d — systemic caution")
    elif tier1_count >= 50:
        score += 0.1
        warnings.append(f"Tier-1 banks: {tier1_count} high-conf bearish signals in 7d — elevated caution")

    # 4.5) Thematic: CSP CapEx spiral (ongoing systemic risk)
    if CSP_CAPEX_RISK:
        score += 0.1
        warnings.append("CSP CapEx spiral: Google/MSFT/AMZN/ORCL massive AI spend → FCF pressure → refinancing risk")
        events.append({"type": "CSP_CAPEX", "severity": "low", "ongoing": True})

    # 5) Earnings reports for tracked holdings
    try:
        conn = sqlite3.connect(str(PROJECT_DIR / "valuation.db"))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM earnings_calendar WHERE date BETWEEN ? AND ?",
            (window_start.strftime("%Y-%m-%d"), window_end.strftime("%Y-%m-%d"))
        ).fetchall()
        conn.close()
        for r in rows:
            d = dict(r)
            score += 0.15
            warnings.append(f"Earnings: {d['company']} ({d['ticker']}) on {d['date']}")
            events.append({"type": "EARNINGS", "ticker": d["ticker"],
                           "company": d["company"], "date": d["date"], "severity": "medium"})
    except Exception:
        pass

    return {
        "score": min(score, 1.0),
        "warnings": warnings,
        "events": events,
    }


def get_event_adjustment() -> float:
    """Return a signal score penalty for high event risk.
    0.0 = no adjustment. Returns negative value to subtract from buy signals.
    """
    risk = get_event_risk()
    s = risk["score"]
    if s >= 0.5:
        return -2.0   # high risk → significant penalty
    elif s >= 0.2:
        return -0.5   # moderate risk → light penalty
    return 0.0
