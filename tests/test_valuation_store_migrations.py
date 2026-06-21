import sqlite3


def test_valuation_store_migrates_legacy_schema(tmp_path):
    db_path = tmp_path / "valuation.db"
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE valuations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_path TEXT NOT NULL UNIQUE,
            pdf_name TEXT NOT NULL,
            company TEXT NOT NULL,
            bank TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE consensus_cache (
            company TEXT NOT NULL,
            metric TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

    from valuation_store import SCHEMA_VERSION, ValuationStore

    store = ValuationStore(db_path)
    val_cols = {r["name"] for r in store.conn.execute("PRAGMA table_info(valuations)").fetchall()}
    cache_cols = {r["name"] for r in store.conn.execute("PRAGMA table_info(consensus_cache)").fetchall()}
    user_version = store.conn.execute("PRAGMA user_version").fetchone()[0]
    store.close()

    assert {"tp_new", "tp_currency", "eps_forecast", "pe_current", "updated_at"} <= val_cols
    assert {"fiscal_year", "median_val", "generated_at"} <= cache_cols
    assert user_version == SCHEMA_VERSION
