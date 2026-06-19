"""Integration tests for bearish_alert pipeline — scan→match→dedup→format."""

import sys, os, json, sqlite3, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from datetime import datetime, timedelta
from pathlib import Path


@pytest.fixture
def temp_db():
    """Create a temporary logic_chains.db with known bearish/bullish signals."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS logic_chains (
            id INTEGER PRIMARY KEY,
            company TEXT, bank TEXT, date TEXT, driver_slug TEXT,
            driver_raw TEXT, direction TEXT, confidence TEXT,
            ticker TEXT, created_at TEXT
        )
    """)
    now = datetime.now()
    yesterday = (now - timedelta(hours=12)).strftime("%Y-%m-%d %H:%M:%S")
    # Bearish signal for SMIC (tracked company)
    conn.execute("INSERT INTO logic_chains VALUES (1, 'Semiconductor Manufacturing International Corporation', 'Nomura', ?, '折旧压力', '折旧压力原文', 'bearish', 'high', '0981', ?)", (now.strftime("%Y-%m-%d"), yesterday))
    # Bullish signal for SMIC
    conn.execute("INSERT INTO logic_chains VALUES (2, 'Semiconductor Manufacturing International Corporation', 'GS', ?, 'Q2指引超预期', 'Q2指引超预期原文', 'bullish', 'high', '0981', ?)", (now.strftime("%Y-%m-%d"), yesterday))
    # Bearish for untracked company
    conn.execute("INSERT INTO logic_chains VALUES (3, 'Random Unknown Corp', 'BankX', ?, '无关信号', '无关', 'bearish', 'medium', 'XXX', ?)", (now.strftime("%Y-%m-%d"), yesterday))
    conn.commit()
    conn.close()
    yield path
    os.unlink(path)


@pytest.fixture
def temp_history(tmp_path):
    """Empty push_history.json in temp dir."""
    p = tmp_path / "push_history.json"
    p.write_text("[]")
    return p


def test_scan_finds_bearish(temp_db, monkeypatch):
    """scan_bearish_signals should return only bearish rows within window."""
    monkeypatch.setattr("bearish_alert.DB_PATH", Path(temp_db))
    from bearish_alert import scan_bearish_signals
    signals = scan_bearish_signals(hours=24)
    assert len(signals) == 2  # 2 bearish (SMIC + Random), not the bullish one


def test_match_tracked_filters_untracked(temp_db, monkeypatch):
    """match_tracked should keep SMIC, drop Random Unknown Corp."""
    monkeypatch.setattr("bearish_alert.DB_PATH", Path(temp_db))
    from bearish_alert import scan_bearish_signals, match_tracked
    raw = scan_bearish_signals(hours=24)
    matched = match_tracked(raw)
    assert len(matched) == 1
    assert "SMIC" in matched[0]["_canonical_name"]


def test_dedup_filters_previously_sent(temp_db, tmp_path, monkeypatch):
    """Signals with same key should be filtered out."""
    monkeypatch.setattr("bearish_alert.DB_PATH", Path(temp_db))
    monkeypatch.setattr("bearish_alert.HISTORY_PATH", tmp_path / "push_history.json")

    from bearish_alert import scan_bearish_signals, match_tracked, load_sent_keys, _signal_key, record_sent

    raw = scan_bearish_signals(hours=24)
    matched = match_tracked(raw)
    # Mark as sent
    for s in matched:
        s["_canonical_name"] = s.get("_canonical_name", "Test")
    record_sent(matched)

    # Now load sent keys — should be non-empty
    sent = load_sent_keys()
    assert len(sent) > 0

    # All matched signals should be in sent_keys
    new = [s for s in matched if _signal_key(s) not in sent]
    assert len(new) == 0


def test_format_email_groups_by_company():
    """format_email should group signals by canonical company name."""
    from bearish_alert import format_email
    signals = [{
        "_canonical_name": "SMIC",
        "bank": "Nomura",
        "confidence": "high",
        "driver_slug": "折旧费用大幅增长压制毛利率",
    }, {
        "_canonical_name": "SMIC",
        "bank": "Goldman Sachs",
        "confidence": "medium",
        "driver_slug": "客户集中度风险",
    }]
    body = format_email(signals)
    assert "SMIC" in body
    assert "Nomura" in body
    assert "Goldman Sachs" in body
    assert "2 signals" in body or "2" in body
    assert "折旧费用" in body
