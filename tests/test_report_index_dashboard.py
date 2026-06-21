import json
import sqlite3


def test_report_index_migrates_dashboard_columns(tmp_path):
    db_path = tmp_path / "report_index.db"
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pdf_name TEXT NOT NULL,
            source_type TEXT NOT NULL DEFAULT 'investment_banking'
        )
    """)
    conn.commit()
    conn.close()

    from report_index import ReportIndex

    idx = ReportIndex(db_path)
    cols = {r["name"] for r in idx.conn.execute("PRAGMA table_info(documents)").fetchall()}
    idx.close()

    assert "alert_severity" in cols


def test_dashboard_summary_uses_indexed_documents(tmp_path):
    from report_index import ReportIndex

    db_path = tmp_path / "report_index.db"
    analysis_path = tmp_path / "Broker-NVIDIA NVDA.US report-260601_analysis.json"
    analysis_path.write_text(
        json.dumps(
            {
                "pdf_name": "Broker-NVIDIA NVDA.US report-260601.pdf",
                "parsed": {
                    "company": "NVDA",
                    "ticker": "NVDA.US",
                    "alert_severity": "high",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    analysis_path.with_suffix("").with_suffix(".md").write_text("NVIDIA summary", encoding="utf-8")

    idx = ReportIndex(db_path)
    doc_id = idx.index_analysis(str(analysis_path))
    summary = idx.get_dashboard_summary()
    idx.close()

    assert doc_id is not None
    assert summary["analyzed"] == 1
    assert summary["active_alerts"] == 1
    assert summary["companies"] == ["NVIDIA"]


def test_index_analysis_is_idempotent_for_dashboard_counts(tmp_path):
    from report_index import ReportIndex

    db_path = tmp_path / "report_index.db"
    analysis_path = tmp_path / "Broker-NVIDIA NVDA.US report-260601_analysis.json"
    analysis_path.write_text(
        json.dumps(
            {
                "pdf_name": "Broker-NVIDIA NVDA.US report-260601.pdf",
                "parsed": {"company": "NVDA", "ticker": "NVDA.US", "alert_severity": "medium"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    idx = ReportIndex(db_path)
    idx.index_analysis(str(analysis_path))
    idx.index_analysis(str(analysis_path))
    summary = idx.get_dashboard_summary()
    idx.close()

    assert summary["analyzed"] == 1
    assert summary["active_alerts"] == 1
