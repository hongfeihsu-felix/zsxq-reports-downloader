"""Shared pytest fixtures for Hermes tests."""

import json
import sqlite3
import pytest
from pathlib import Path

# ---- Sample markdowns ----
from tests.fixtures.sample_markdowns import (
    EPS_TABLE_JP, EPS_TABLE_TWD, EPS_TABLE_SIMPLE, EPS_BULLET, EPS_NEGATIVE, NO_EPS,
    EPS_SECOND_COL, EPS_JP_FY, EPS_PROSE, PE_FALSE_POSITIVE_1, PE_FALSE_POSITIVE_2,
    PE_CHINESE_FTM, PE_ENGLISH, PE_IMPLIED, PE_NONSTANDARD, PE_NO_MENTION,
    METHOD_PE, METHOD_DCF, METHOD_RI, METHOD_SOTP, METHOD_EV_EBITDA, METHOD_PB,
    METHOD_MULTI, METHOD_NONE,
    TP_KOREAN_WON, TP_NO_TP, TP_SINGLE, TP_RAISED, TP_FILTER_TOO_SMALL,
    MEDIATEK_REPORT_1, MEDIATEK_REPORT_2, MEDIATEK_REPORT_3, MEDIATEK_REPORT_4,
    CLEAN_EPS_REPORTS,
)

# ---- Markdown fixtures ----

@pytest.fixture
def eps_table_jp():
    return EPS_TABLE_JP

@pytest.fixture
def eps_table_twd():
    return EPS_TABLE_TWD

@pytest.fixture
def eps_table_simple():
    return EPS_TABLE_SIMPLE

@pytest.fixture
def eps_bullet():
    return EPS_BULLET

@pytest.fixture
def eps_negative():
    return EPS_NEGATIVE

@pytest.fixture
def no_eps():
    return NO_EPS

@pytest.fixture
def pe_chinese_ftm():
    return PE_CHINESE_FTM

@pytest.fixture
def pe_english():
    return PE_ENGLISH

@pytest.fixture
def pe_implied():
    return PE_IMPLIED

@pytest.fixture
def pe_nonstandard():
    return PE_NONSTANDARD

@pytest.fixture
def pe_no_mention():
    return PE_NO_MENTION

@pytest.fixture
def method_pe():
    return METHOD_PE

@pytest.fixture
def method_dcf():
    return METHOD_DCF

@pytest.fixture
def method_ri():
    return METHOD_RI

@pytest.fixture
def method_sotp():
    return METHOD_SOTP

@pytest.fixture
def method_ev_ebitda():
    return METHOD_EV_EBITDA

@pytest.fixture
def method_pb():
    return METHOD_PB

@pytest.fixture
def method_multi():
    return METHOD_MULTI

@pytest.fixture
def method_none():
    return METHOD_NONE

@pytest.fixture
def tp_korean_won():
    return TP_KOREAN_WON

@pytest.fixture
def tp_no_tp():
    return TP_NO_TP

@pytest.fixture
def tp_single():
    return TP_SINGLE

@pytest.fixture
def tp_raised():
    return TP_RAISED

@pytest.fixture
def tp_filter_too_small():
    return TP_FILTER_TOO_SMALL

# ---- Report snapshots ----

@pytest.fixture
def mediatek_reports():
    return [MEDIATEK_REPORT_1, MEDIATEK_REPORT_2, MEDIATEK_REPORT_3, MEDIATEK_REPORT_4]

@pytest.fixture
def single_report():
    return [MEDIATEK_REPORT_1]

@pytest.fixture
def empty_reports():
    return []

@pytest.fixture
def clean_eps_reports():
    return CLEAN_EPS_REPORTS

@pytest.fixture
def eps_second_col():
    return EPS_SECOND_COL

@pytest.fixture
def eps_jp_fy():
    return EPS_JP_FY

@pytest.fixture
def eps_prose():
    return EPS_PROSE

@pytest.fixture
def pe_false_positive_1():
    return PE_FALSE_POSITIVE_1

@pytest.fixture
def pe_false_positive_2():
    return PE_FALSE_POSITIVE_2


@pytest.fixture
def temp_valuation_db(tmp_path):
    """Create a hermetic valuation.db with enough rows for store/consensus tests."""
    from valuation_store import ValuationStore

    db_path = tmp_path / "valuation.db"
    store = ValuationStore(db_path)

    rows = []
    for i, tp in enumerate([5000, 4200, 5088, 3050, 4600, 4750, 4400, 4900, 5100, 4300], 1):
        rows.append((
            f"/tmp/mediatek_{i}_analysis.json",
            f"Bank{i}-MediaTek report-2605{i:02d}.pdf",
            "MediaTek",
            f"Bank{i}",
            f"2026-05-{i:02d}",
            "Buy",
            float(tp),
            float(tp - 200),
            "TWD",
            json.dumps({"FY26E": 63.0 + i, "FY27E": 110.0 + i}, ensure_ascii=False),
            30.0,
            None,
            "PE",
            2,
        ))

    for i, tp in enumerate([280, 300, 288], 1):
        rows.append((
            f"/tmp/nvidia_{i}_analysis.json",
            f"Bank{i}-NVIDIA Corp NVDA.US report-2605{i:02d}.pdf",
            "NVIDIA",
            f"Bank{i}",
            f"2026-05-{i:02d}",
            "Buy",
            float(tp),
            float(tp - 20),
            "USD",
            json.dumps({"FY27E": 10.0 + i}, ensure_ascii=False),
            28.0,
            None,
            "PE",
            2,
        ))

    rows.append((
        "/tmp/dell_legacy_analysis.json",
        "DELL_20240515_0146.pdf",
        "Dell Technologies Inc",
        "LegacyBank",
        "",
        "Buy",
        152.0,
        None,
        "USD",
        None,
        None,
        None,
        "",
        0,
    ))

    store.conn.executemany(
        """INSERT INTO valuations
           (report_path, pdf_name, company, bank, report_date, rating,
            tp_new, tp_old, tp_currency, eps_forecast,
            pe_current, pe_historical, valuation_method, eps_quality)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )

    store.conn.execute("""
        CREATE TABLE IF NOT EXISTS earnings_actuals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company TEXT NOT NULL,
            period TEXT NOT NULL,
            eps_actual REAL
        )
    """)
    store.conn.executemany(
        "INSERT INTO earnings_actuals (company, period, eps_actual) VALUES (?, ?, ?)",
        [("NVIDIA", "2026Q1", 1.0), ("NVIDIA", "2026Q2", 1.1), ("NVIDIA", "2026Q3", 1.2)],
    )
    store.conn.commit()
    store.close()

    return db_path
