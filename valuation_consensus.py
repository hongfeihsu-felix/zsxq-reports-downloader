#!/usr/bin/env python3
"""Valuation consensus computation — median, outlier detection, confidence scoring."""

import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

PROJECT_DIR = Path(__file__).parent


def _median(vals: list[float]) -> float:
    if not vals:
        return 0.0
    sv = sorted(vals)
    n = len(sv)
    if n % 2 == 0:
        return (sv[n // 2 - 1] + sv[n // 2]) / 2.0
    return float(sv[n // 2])


def _iqr_filter(values: list[float]) -> tuple[list[float], list[float]]:
    """IQR outlier detection. Returns (inliers, outliers)."""
    if len(values) < 3:
        return values, []
    sv = sorted(values)
    n = len(sv)
    q1 = sv[n // 4]
    q3 = sv[3 * n // 4]
    iqr = q3 - q1
    if iqr == 0:
        return values, []
    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr
    inliers = [v for v in values if lower <= v <= upper]
    outliers = [v for v in values if v < lower or v > upper]
    return inliers, outliers


def compute_consensus(reports: list[dict]) -> dict:
    """从报告列表计算估值共识。

    返回: {
        "method": str, "cs_eps": {FY26E: val, ...}, "cs_pe": float, "cs_tp": float,
        "currency": str, "ratings": {Buy: 3, Hold: 1},
        "eps_quality": "low"|"medium"|"high",
        "eps_outliers": [str], "warning": str or None,
    }
    """
    if not reports:
        return {"method": "N/A", "has_data": False}

    # Valuation method consensus
    methods = Counter(r.get("method", "") for r in reports if r.get("method"))
    top_method = methods.most_common(1)[0][0] if methods else "PE"

    # Rating distribution
    ratings = Counter(r.get("rating", "") for r in reports if r.get("rating"))

    # Collect EPS by fiscal year
    eps_by_year = defaultdict(list)
    for r in reports:
        eps = r.get("eps_forecast", {})
        if isinstance(eps, str):
            try:
                eps = json.loads(eps)
            except Exception:
                continue
        for yr, v in eps.items():
            if isinstance(v, (int, float)) and v > 0:
                eps_by_year[yr].append(v)

    # Filter outliers per year, compute median
    cs_eps = {}
    eps_outliers = []
    quality_scores = []
    for yr, vals in sorted(eps_by_year.items()):
        inliers, outliers = _iqr_filter(vals)
        if inliers:
            cs_eps[yr] = round(_median(inliers), 2)
        for o in outliers:
            eps_outliers.append(f"{yr}: {o}")

    # Estimate EPS quality
    if cs_eps:
        outlier_ratio = len(eps_outliers) / max(1, sum(len(v) for v in eps_by_year.values()))
        if outlier_ratio < 0.1 and len(reports) >= 3:
            eps_quality = "high"
        elif outlier_ratio < 0.3:
            eps_quality = "medium"
        else:
            eps_quality = "low"
    else:
        eps_quality = "none"

    # PE consensus
    pe_vals = [r.get("pe") for r in reports if r.get("pe")]
    pe_inliers, _ = _iqr_filter(pe_vals) if len(pe_vals) >= 3 else (pe_vals, [])
    cs_pe = round(_median(pe_inliers), 1) if pe_inliers else 0

    # TP consensus (most reliable — directly from broker reports)
    tp_vals = [r["tp_new"] for r in reports if r.get("tp_new")]
    tp_inliers, tp_outliers = _iqr_filter(tp_vals) if len(tp_vals) >= 3 else (tp_vals, [])
    cs_tp = round(_median(tp_inliers), 1) if tp_inliers else 0
    currency = reports[0].get("tp_currency", "")

    # Warning: EPS×PE vs consensus TP
    warning = None
    latest_eps = list(cs_eps.values())[-1] if cs_eps else 0
    if latest_eps and cs_pe and cs_tp:
        implied = latest_eps * cs_pe
        ratio = implied / cs_tp if cs_tp else 1
        if ratio > 1.5:
            warning = (f"EPS×PE implied ({implied:,.0f} {currency}) is "
                       f"{ratio:.1f}x the broker consensus TP ({cs_tp:,.0f} {currency}). "
                       f"EPS data may contain errors. Trust broker TP as primary.")
        elif ratio < 0.5:
            warning = (f"EPS×PE implied ({implied:,.0f} {currency}) is "
                       f"significantly below broker consensus. EPS/PE may be incomplete.")

    return {
        "has_data": True,
        "method": top_method,
        "cs_eps": cs_eps,
        "cs_pe": cs_pe,
        "cs_tp": cs_tp,
        "currency": currency,
        "ratings": dict(ratings),
        "eps_quality": eps_quality,
        "eps_outliers": eps_outliers,
        "warning": warning,
    }


def compute_peers_consensus(peer_valuations: list[dict]) -> list[dict]:
    """从同行估值数据中提取 EPS/PE/TP 摘要。"""
    return [
        {
            "name": p["name"],
            "pe": p.get("pe"),
            "eps": p.get("eps"),
            "tp": p.get("tp"),
            "tp_currency": p.get("tp_currency", ""),
        }
        for p in peer_valuations
    ]
