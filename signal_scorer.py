#!/usr/bin/env python3
"""
Shared signal scoring — reusable by realtime_alert, backtest, and dashboard.

Weights are loaded from config.json → realtime_alert section.
Pure functions: no network, no DB, deterministic.
"""

import json
from pathlib import Path
from datetime import datetime

PROJECT_DIR = Path(__file__).parent
CONFIG_PATH = PROJECT_DIR / "config.json"


def _load_weights():
    if CONFIG_PATH.exists():
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return cfg.get("realtime_alert", {})
    return {}


_CFG = _load_weights()
SCORE_BUY = _CFG.get("score_threshold_buy", 3)
SCORE_SELL = _CFG.get("score_threshold_sell", -3)
SCORE_BUY_NOW = _CFG.get("score_threshold_buy_now", 4)
SCORE_WATCH = _CFG.get("score_threshold_watch", 2)


def _sell_action(score: float, buy_threshold: float, sell_threshold: float) -> str:
    if score >= buy_threshold:
        return "BUY"
    if score <= sell_threshold:
        return "SELL"
    return "HOLD"


def _buy_action(score: float, buy_now_threshold: float, watch_threshold: float) -> str:
    if score >= buy_now_threshold:
        return "BUY_NOW"
    if score >= watch_threshold:
        return "WATCH"
    return "WAIT"


# ── Technical Helpers ───────────────────────────────────

def ema(data: list[float], period: int) -> float:
    """Exponential moving average."""
    if len(data) < period:
        return sum(data) / len(data) if data else 0
    k = 2 / (period + 1)
    result = sum(data[:period]) / period
    for v in data[period:]:
        result = v * k + result * (1 - k)
    return result


def detect_regime(closes: list[float], highs: list[float], lows: list[float]) -> str:
    """Detect bullish/neutral/bearish from daily bars. Port of TrendDetector."""
    n = len(closes)
    if n < 55:
        return "neutral"
    current = closes[-1]
    ma20 = sum(closes[-20:]) / 20
    ma50 = sum(closes[-50:]) / 50
    ma_slope = (ma20 - sum(closes[-25:-5]) / 20) / (ma20 + 0.001)

    def _atr(hi, lo, cl, window):
        trs = []
        for i in range(1, min(window, len(cl))):
            trs.append(max(hi[-i] - lo[-i], abs(hi[-i] - cl[-i-1]), abs(lo[-i] - cl[-i-1])))
        return sum(trs) / len(trs) if trs else 0

    atr_now = _atr(highs, lows, closes, 14)
    atr_hist = _atr(highs, lows, closes, 60)
    vol_spike = (atr_now / atr_hist) > 1.5 if atr_hist > 0 else False

    above_ma20 = current > ma20
    ma_golden = ma20 > ma50
    trend_score = sum([above_ma20 * 0.4, ma_golden * 0.3,
                       (ma_slope > 0) * 0.2, (not vol_spike) * 0.1])

    if trend_score >= 0.7 and not vol_spike:
        return "bullish"
    elif trend_score >= 0.4:
        return "neutral"
    else:
        return "bearish"


def compute_technicals(bars: list, session_hour: int = None) -> dict:
    """Compute MA/MACD/Regime/Volume from daily bars → score.

    Args:
        bars: list of dicts with close/high/low/volume
        session_hour: Beijing hour of check (11 for A-share 11AM, 23 for US 11:30AM ET).
                      Used to scale partial-day volume. None = full day.
    """
    if len(bars) < 26:
        return {"ma_position": "unknown", "macd_status": "unknown",
                "regime": "neutral", "volume_ratio": 1.0, "technical_score": 0.0}

    # Time-of-day volume scaling (partial day → estimate full-day)
    vol_scale = _session_volume_scale(session_hour) if session_hour else 1.0

    closes = [b.get('close', 0) if isinstance(b, dict) else getattr(b, 'close', 0) for b in bars]
    highs = [b.get('high', 0) if isinstance(b, dict) else getattr(b, 'high', 0) for b in bars]
    lows = [b.get('low', 0) if isinstance(b, dict) else getattr(b, 'low', 0) for b in bars]
    vols = [b.get('volume', 0) if isinstance(b, dict) else getattr(b, 'volume', 0) for b in bars]
    # Scale today's partial volume to estimated full-day volume
    if vol_scale != 1.0:
        vols[-1] = vols[-1] * vol_scale
    current = closes[-1]
    n = len(closes)

    # MA
    ma20 = sum(closes[-20:]) / 20
    ma50 = sum(closes[-50:]) / 50 if n >= 50 else ma20
    if current > ma20 and current > ma50:
        ma_pos = "above_20_50"
    elif current > ma20:
        ma_pos = "above_20"
    else:
        ma_pos = "below"

    # MACD
    try:
        ef = ema(closes, 12)
        es = ema(closes, 26)
        ml = ef - es
        recent = [ema(closes[:i+1], 12) - ema(closes[:i+1], 26) for i in range(26, n)]
        sl = ema(recent, 9) if len(recent) >= 9 else ml
        prev_ml = ema(closes[:-1], 12) - ema(closes[:-1], 26)
        dn = ml - sl
        dp = prev_ml - sl
        if dp <= 0 < dn:
            macd = "golden_cross"
        elif dp >= 0 > dn:
            macd = "death_cross"
        elif dn > 0:
            macd = "bullish"
        else:
            macd = "bearish"
    except Exception:
        macd = "unknown"

    # Regime
    regime = detect_regime(closes, highs, lows)

    # Volume anomaly
    avg_vol = sum(vols[-20:]) / min(20, n)
    vol_ratio = (vols[-1] / avg_vol) if avg_vol > 0 else 1.0

    # Score
    score = 0.0
    score += {"above_20_50": 1, "above_20": 0.5, "below": -1}.get(ma_pos, 0)
    score += {"golden_cross": 2, "bullish": 1, "bearish": -1, "death_cross": -2, "unknown": 0}.get(macd, 0)
    score += {"bullish": 2, "neutral": 0, "bearish": -2}.get(regime, 0)
    if vol_ratio > 1.5 and score > 0:
        score += 1
    elif vol_ratio > 1.5 and score < 0:
        score -= 1

    return {"ma_position": ma_pos, "macd_status": macd,
            "regime": regime, "volume_ratio": round(vol_ratio, 2),
            "technical_score": round(score, 1)}


# ── Composite Signals ───────────────────────────────────

def compute_sell_signal(research: dict, technicals: dict, price: float,
                        cost_basis: float = 0.0,
                        buy_threshold: float = None, sell_threshold: float = None) -> dict:
    """Score for existing position (direction=SELL).

    Args:
        cost_basis: position cost basis. If > 0, profit pressure is applied
                    to enforce take-profit discipline.
    """
    _buy = buy_threshold if buy_threshold is not None else SCORE_BUY
    _sell = sell_threshold if sell_threshold is not None else SCORE_SELL

    r_score = research.get("research_score", 0)
    t_score = technicals.get("technical_score", 0)
    score = r_score * 0.4 + t_score * 0.4

    # ── Profit discipline ──
    profit_note = ""
    if cost_basis > 0 and price > 0:
        gain_pct = (price - cost_basis) / cost_basis

        # Sector momentum modifier: in super-cycles, relax profit thresholds
        sector_momentum = min((r_score + t_score) / 15.0, 1.0)
        # momentum=0 → thresholds unchanged (50%/80%)
        # momentum=1 → thresholds raised (100%/150%)
        warn_50 = 0.5 + 0.5 * sector_momentum   # 50% → 100%
        warn_80 = 0.8 + 0.7 * sector_momentum   # 80% → 150%

        # Detect peak drawdown (right-side exit)
        peak_draw = 0.0
        if technicals.get("ma_position") == "below" and gain_pct > 0.3:
            peak_draw = 0.5
        if technicals.get("macd_status") in ("death_cross", "bearish") and gain_pct > 0.3:
            peak_draw += 0.5

        if gain_pct > warn_80:
            score -= 3.0 + peak_draw - sector_momentum * 1.5  # super-cycle softens penalty
            profit_note = f"🔴 GAIN:{gain_pct:+.0%} (threshold:{warn_80:+.0%}) — extreme profit, TAKE PROFIT NOW"
        elif gain_pct > warn_50:
            score -= 2.0 + peak_draw - sector_momentum * 1.0
            profit_note = f"🟠 GAIN:{gain_pct:+.0%} (threshold:{warn_50:+.0%}) — heavy profit, tighten stop"
            if peak_draw > 0:
                profit_note += " [PEAK FADING: right-side exit]"
            if sector_momentum > 0.6:
                profit_note += " [SUPER-CYCLE: relaxed]"
        elif gain_pct > 0.3:
            score -= 1.0 + peak_draw - sector_momentum * 0.5
            profit_note = f"🟡 GAIN:{gain_pct:+.0%} — profit target reached"
            if peak_draw > 0:
                profit_note += " [fading from peak]"
        elif gain_pct > 0.15:
            profit_note = f"GAIN:{gain_pct:+.0%} — tracking"
        elif gain_pct < -0.15:
            score -= 1.0
            profit_note = f"🔻 LOSS:{gain_pct:+.0%} — underwater, holding"

    tp = research.get("consensus_tp")
    v_detail = ""
    ref_price = None
    if tp and price > 0:
        ref_price = tp * 0.8  # 参考卖出价 = 共识TP × 80%
        upside = (tp / price) - 1
        ratio = price / ref_price
        if price <= ref_price:
            score -= 3.0  # 跌破参考价 → 强卖出信号
        elif ratio < 1.1:
            score -= 1.0  # 接近参考价 → 警告
        elif ratio < 1.3:
            score += 0.5  # 距参考价较远 → 安全
        else:
            score += 1.0  # 远超参考价 → 持有
        v_detail = f"TP:{tp:.0f} ref:{ref_price:.0f} now:{price:.2f} ({ratio:.0%}x)"

    # Macro event risk adjustment
    from macro_events import get_event_adjustment
    event_penalty = get_event_adjustment()
    if event_penalty != 0:
        score += event_penalty

    action = _sell_action(score, _buy, _sell)

    # Hard constraint: profit discipline overrides research. Keep the displayed
    # score aligned with a forced SELL so downstream tables don't show SELL
    # with a non-sell score.
    if cost_basis > 0 and price > 0:
        if gain_pct > warn_80 and action != "SELL":
            action = "SELL"
            score = min(score, _sell)
        elif gain_pct > warn_50 and action == "BUY":
            action = "HOLD"

    details = _build_details(research, technicals, v_detail)
    if profit_note:
        details.insert(0, profit_note)
    return {
        "score": round(score, 2), "action": action,
        "research_score": r_score,
        "technical_score": technicals.get("technical_score", 0),
        "valuation_score": round(score - r_score * 0.4 - t_score * 0.4, 2),
        "details": " | ".join(details),
        "tp": tp,
        "top_bearish": research.get("top_bearish", []),
        "top_bullish": research.get("top_bullish", []),
    }


def compute_buy_signal(research: dict, technicals: dict, price: float,
                       target_price: float,
                       buy_now_threshold: float = None,
                       watch_threshold: float = None) -> dict:
    """Score for watchlist entry (direction=BUY)."""
    _buy_now = buy_now_threshold if buy_now_threshold is not None else SCORE_BUY_NOW
    _watch = watch_threshold if watch_threshold is not None else SCORE_WATCH

    r_score = research.get("research_score", 0)
    t_score = technicals.get("technical_score", 0)
    score = r_score * 0.3 + t_score * 0.25

    # Price proximity to target
    proximity_detail = ""
    if target_price > 0 and price > 0:
        ratio = price / target_price
        if ratio <= 1.02:
            prox = 3.0
        elif ratio <= 1.05:
            prox = 2.4
        elif ratio <= 1.10:
            prox = 1.5
        elif ratio <= 1.20:
            prox = 0.5
        else:
            prox = 0.0
        score += prox * 0.3
        proximity_detail = f"target:{target_price:.2f} vs {price:.2f} ({ratio:.0%})"
    else:
        proximity_detail = "target:missing"

    # Valuation safety margin
    tp = research.get("consensus_tp")
    if tp and price > 0:
        upside = (tp / price) - 1
        if upside > 0.3:
            score += 0.25
        elif upside > 0.1:
            score += 0.15
        elif upside < -0.1:
            score -= 0.1

    # Macro event risk adjustment
    from macro_events import get_event_adjustment
    event_penalty = get_event_adjustment()
    if event_penalty != 0:
        score += event_penalty

    action = _buy_action(score, _buy_now, _watch)

    # Hard constraints: trading BUY signals need an explicit entry target.
    if target_price <= 0:
        action = "WAIT"
    elif action == "BUY_NOW" and price > target_price * 1.05:
        action = "WATCH"

    details = _build_details(research, technicals, proximity_detail)
    return {
        "score": round(score, 2), "action": action,
        "research_score": r_score,
        "technical_score": technicals.get("technical_score", 0),
        "valuation_score": round(score - r_score * 0.3 - t_score * 0.25, 2),
        "details": " | ".join(details),
        "tp": tp,
        "top_bearish": research.get("top_bearish", []),
        "top_bullish": research.get("top_bullish", []),
    }


def _session_volume_scale(hour: int) -> float:
    """Estimate full-day volume from partial session volume.

    Beijing time reference:
      A-share: 9:30-11:30 + 13:00-15:00. At 11:00, ~30% of day elapsed → 3.3x
      US:      9:30-16:00 ET = 21:30-04:00 Beijing. At 23:30, ~31% elapsed → 3.2x
      HK:      9:30-12:00 + 13:00-16:00. At 11:00, ~38% elapsed → 2.6x

    Returns scaling factor. 1.0 = no adjustment needed.
    """
    if 9 <= hour <= 11:    # A-share / HK mid-session (11:00 AM Beijing)
        return 3.0          # ~33% of day → scale 3x
    elif 21 <= hour <= 23: # US session (21:30-04:00 Beijing)
        return 3.0          # ~31% of day at 23:30, even less at 21:30 → scale 3x
    return 1.0               # outside mid-session windows → no adjustment


def _build_details(research: dict, technicals: dict, extra: str = "") -> list[str]:
    """Build human-readable details list for signal output."""
    d = []
    bh = research.get("bearish_high", 0)
    bm = research.get("bearish_medium", 0)
    if bh or bm:
        d.append(f"bearish:{bh}H/{bm}M")
    buh = research.get("bullish_high", 0)
    bum = research.get("bullish_medium", 0)
    if buh or bum:
        d.append(f"bullish:{buh}H/{bum}M")
    d.append(f"MA:{technicals.get('ma_position','?')} MACD:{technicals.get('macd_status','?')}")
    d.append(f"regime:{technicals.get('regime','?')} vol:{technicals.get('volume_ratio',1)}x")
    if extra:
        d.append(extra)
    return d
