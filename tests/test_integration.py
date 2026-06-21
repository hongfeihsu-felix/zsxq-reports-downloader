"""Integration tests — DB read/write + extraction on real data."""

import sys, pytest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestStoreRoundTrip:
    """Verify valuation_store write → read consistency."""

    def test_backfill_produces_data(self):
        from valuation_store import ValuationStore
        store = ValuationStore()
        companies = store.get_all_companies()
        assert len(companies) >= 100, f"Expected >=100 companies, got {len(companies)}"
        store.close()

    def test_nvidia_has_earnings(self):
        import sqlite3
        conn = sqlite3.connect(str(Path(__file__).parent.parent / "valuation.db"))
        rows = conn.execute(
            "SELECT COUNT(*) FROM earnings_actuals WHERE company='NVIDIA'"
        ).fetchone()[0]
        conn.close()
        assert rows >= 3, f"NVIDIA should have >=3 quarters actual EPS, got {rows}"

    def test_mediatek_consensus_sane(self):
        from valuation_store import ValuationStore
        from valuation_consensus import compute_consensus
        store = ValuationStore()
        reports = store.get_by_company("MediaTek")
        assert len(reports) >= 10, f"MediaTek has {len(reports)} reports, expected >=10"
        consensus = compute_consensus(reports)
        assert consensus["has_data"] is True
        assert consensus["cs_tp"] > 100  # Should be several thousand TWD
        store.close()

    def test_company_lookup_accepts_ticker_alias(self):
        """Ticker aliases such as NVDA should resolve to canonical DB company names."""
        from valuation_store import ValuationStore
        store = ValuationStore()
        by_name = store.get_by_company("NVIDIA")
        by_ticker = store.get_by_company("NVDA")
        store.close()
        assert by_name, "NVIDIA should have valuation reports"
        assert by_ticker, "NVDA alias should resolve to NVIDIA valuation reports"
        assert len(by_ticker) == len(by_name)

    def test_undated_tp_rows_are_visible(self):
        """Legacy rows with tp_new but blank report_date should not disappear."""
        from valuation_store import ValuationStore
        store = ValuationStore()
        reports = store.get_by_company("Dell Technologies Inc")
        store.close()
        assert any(r.get("tp_new") for r in reports), (
            "Dell has a TP-bearing legacy row with blank report_date and should be returned"
        )

    def test_consensus_tp_near_broker_median(self):
        """All companies with >=3 reports: consensus TP within 30% of broker median."""
        from valuation_store import ValuationStore
        from valuation_consensus import compute_consensus, _median
        store = ValuationStore()
        failures = []
        for co in store.get_all_companies():
            reports = store.get_by_company(co)
            if len(reports) < 3:
                continue
            consensus = compute_consensus(reports)
            cs_tp = consensus.get("cs_tp", 0)
            broker_tps = [r["tp_new"] for r in reports if r.get("tp_new")]
            if not broker_tps or cs_tp == 0:
                continue
            raw_median = _median(broker_tps)
            if raw_median == 0:
                continue
            deviation = abs(cs_tp - raw_median) / raw_median * 100
            if deviation > 30:
                failures.append(f"{co}: cs={cs_tp:.0f} broker_med={raw_median:.0f} dev={deviation:.1f}%")
        store.close()
        assert len(failures) == 0, f"{len(failures)} companies exceed 30% TP deviation:\n" + "\n".join(failures[:10])


class TestExtractionOnRealData:
    """Run extractors on 5 randomly sampled real reports, verify they produce data."""

    def test_extract_eps_from_semco(self):
        from vision_parser import extract_eps_forecasts
        report_base = Path.home() / "hermes_reports" / "Investment_Banking_Report"
        md = list(report_base.glob("20260522/*Samsung*Electro*capacitor*_analysis.md"))
        if not md:
            pytest.skip("SEMCO report not found")
        eps = extract_eps_forecasts(md[0].read_text(encoding="utf-8"))
        assert len(eps) >= 3, f"SEMCO should have >=3 EPS values, got {eps}"

    def test_extract_eps_from_nvidia(self):
        from vision_parser import extract_eps_forecasts
        report_base = Path.home() / "hermes_reports" / "Investment_Banking_Report"
        md = list(report_base.glob("20260522/*NVIDIA CorpNVDA*_analysis.md"))
        if not md:
            pytest.skip("NVIDIA report not found")
        eps = extract_eps_forecasts(md[0].read_text(encoding="utf-8"))
        assert len(eps) >= 3, f"NVIDIA should have >=3 EPS values, got {eps}"
