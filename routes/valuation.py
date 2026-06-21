"""Valuation dashboard routes."""

import json
import sqlite3
from pathlib import Path

from flask import Blueprint, render_template, request

from valuation_consensus import compute_consensus, compute_peers_consensus
from valuation_store import ValuationStore


bp = Blueprint("valuation", __name__)

PROJECT_DIR = Path(__file__).resolve().parents[1]


def _load_matrix():
    """Load Company-Industry Matrix."""
    matrix_path = PROJECT_DIR / "industry_matrix.json"
    if matrix_path.exists():
        return json.loads(matrix_path.read_text(encoding="utf-8"))
    return {"companies": {}, "industries": {}, "unmapped": []}


@bp.route("/valuation")
def view_valuation():
    """Company valuation model."""
    company = request.args.get("company", "").strip()
    store = ValuationStore(PROJECT_DIR / "valuation.db")

    try:
        co_list = store.get_all_companies()
        if not co_list:
            matrix = _load_matrix()
            co_list = sorted(matrix.get("companies", {}).keys())

        if not company:
            return render_template("valuation_selector.html", companies=co_list)

        reports = store.get_by_company(company)
        if not reports:
            return render_template("valuation_no_data.html", company=company, companies=co_list)
        company = reports[0].get("company") or company

        consensus = compute_consensus(reports)

        matrix = _load_matrix()
        co_info = matrix.get("companies", {}).get(company, {})
        ind_slug = co_info.get("industry_slug", "")
        ind_name = co_info.get("industry", "")

        peers = []
        if ind_slug:
            peer_names = [
                p for p in matrix.get("industries", {}).get(ind_slug, {}).get("companies", [])
                if p != company
            ][:8]
            peer_vals = store.get_peers(peer_names)
            peers = compute_peers_consensus(peer_vals)

        actuals = []
        try:
            econn = sqlite3.connect(str(PROJECT_DIR / "valuation.db"))
            econn.row_factory = sqlite3.Row
            actuals = [
                dict(r) for r in econn.execute(
                    "SELECT * FROM earnings_actuals WHERE company=? ORDER BY period DESC LIMIT 4",
                    (company,),
                ).fetchall()
            ]
            econn.close()
        except Exception:
            pass

        return render_template(
            "valuation.html",
            company=company,
            consensus=consensus,
            reports=reports[:10],
            peers=peers,
            actuals=actuals,
            ind_slug=ind_slug,
            ind_name=ind_name,
        )
    finally:
        store.close()
