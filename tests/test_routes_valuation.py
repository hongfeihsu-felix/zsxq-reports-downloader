import json

from flask import Flask


def _make_app(template_dir):
    from routes.valuation import bp

    app = Flask(__name__, template_folder=str(template_dir))
    app.register_blueprint(bp)
    return app


def test_valuation_selector_route_uses_temp_db(temp_valuation_db, monkeypatch):
    import routes.valuation as valuation_routes

    monkeypatch.setattr(valuation_routes, "PROJECT_DIR", temp_valuation_db.parent)
    (temp_valuation_db.parent / "industry_matrix.json").write_text(
        json.dumps({"companies": {}, "industries": {}, "unmapped": []}),
        encoding="utf-8",
    )

    app = _make_app(valuation_routes.Path(__file__).resolve().parents[1] / "templates")
    resp = app.test_client().get("/valuation")

    assert resp.status_code == 200
    assert b"NVIDIA" in resp.data


def test_valuation_company_route_renders_consensus(temp_valuation_db, monkeypatch):
    import routes.valuation as valuation_routes

    monkeypatch.setattr(valuation_routes, "PROJECT_DIR", temp_valuation_db.parent)
    (temp_valuation_db.parent / "industry_matrix.json").write_text(
        json.dumps(
            {
                "companies": {"NVIDIA": {"industry_slug": "ai-chip", "industry": "AI Chip"}},
                "industries": {"ai-chip": {"companies": ["NVIDIA", "MediaTek"]}},
                "unmapped": [],
            }
        ),
        encoding="utf-8",
    )

    app = _make_app(valuation_routes.Path(__file__).resolve().parents[1] / "templates")
    resp = app.test_client().get("/valuation?company=NVDA")

    assert resp.status_code == 200
    assert b"NVIDIA" in resp.data
    assert b"USD" in resp.data
