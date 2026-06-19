# Realtime Alert System — Architecture Document

> Version: 1.0 | Date: 2026-06-11 | Author: Hermes × QuanTrading

## 1. Overview

The Realtime Alert Subsystem monitors the user's portfolio (holdings + watchlist) across A-share, US, and HK markets, and generates actionable BUY/SELL/HOLD signals via email at scheduled intervals.

```
┌──────────────────────────────────────────────────────────────────┐
│                        DATA SOURCES                               │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐ │
│  │ QMT      │  │ Finnhub  │  │ yfinance │  │ Hermes           │ │
│  │ (A-share │  │ (US      │  │ (US/HK   │  │ logic_chains.db  │ │
│  │  realtime)│  │  realtime)│  │  daily)  │  │ valuation.db     │ │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────────┬─────────┘ │
└───────┼──────────────┼──────────────┼─────────────────┼──────────┘
        │              │              │                 │
        ▼              ▼              ▼                 ▼
┌──────────────────────────────────────────────────────────────────┐
│                    realtime_alert.py                               │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────────────┐ │
│  │ fetch       │  │ get_current  │  │ get_research_signals     │ │
│  │ holdings    │  │ _price       │  │ (logic_chains + TP)      │ │
│  │ (holdings.db│  │ (QMT→Finnhub │  │                          │ │
│  │  + config)  │  │  →yfinance)  │  │ AH premium for A/H cross │ │
│  └──────┬──────┘  └──────┬───────┘  └───────────┬──────────────┘ │
│         │                │                      │                │
│         ▼                ▼                      ▼                │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │              signal_scorer.py (shared module)                │ │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │ │
│  │  │ compute_     │  │ compute_sell │  │ compute_buy      │  │ │
│  │  │ technicals   │  │ _signal      │  │ _signal          │  │ │
│  │  │ (MA/MACD/    │  │ (40%R+40%T   │  │ (30%R+25%T+30%   │  │ │
│  │  │  Regime/Vol) │  │  +20%V)      │  │  Price+15%V)     │  │ │
│  │  └──────────────┘  └──────────────┘  └──────────────────┘  │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                              │                                    │
│                              ▼                                    │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  format_email() → SMTP → hongfeihsu@foxmail.com              │ │
│  │  dedup via push_history.json                                 │ │
│  └─────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

## 2. Scheduling

| Market | Time (Beijing) | Trigger | Data Path |
|--------|---------------|---------|-----------|
| A-share | 11:00 Mon-Fri | launchd `com.hermes.realtime-alert-ashare` | QMT price + baostock daily bars |
| US | 23:30 Mon-Fri | launchd `com.hermes.realtime-alert-us` | Finnhub price + yfinance daily bars |

HK stocks are included in the US run (23:30 Beijing = 11:30 HKT).

## 3. Scoring Model

### 3.1 Sell Signal (Existing Positions)

Purpose: Determine if holdings should be reduced or held.

| Component | Weight | Source | Logic |
|-----------|--------|--------|-------|
| Research | 40% | logic_chains.db | Bullish count − Bearish count (high-confidence ×2) |
| Technical | 40% | Daily bars | MA position + MACD crossover + Regime state + Volume anomaly |
| Valuation | 20% | Consensus TP | Sell reference = TP × 80%. Price below reference → −3.0 penalty |

**Thresholds:** score ≥ 3 → BUY (add), score ≤ −3 → SELL (reduce), else HOLD.

**Valuation Detail:** The sell reference price is derived from analyst consensus target price:
- `ref_price = consensus_TP × 80%`
- Current price ≤ ref_price → strong SELL signal (−3.0)
- Current price within 110% of ref_price → warning (−1.0)
- Current price > 130% of ref_price → safe (+1.0)

For A-share stocks cross-listed with H-shares (e.g., SMIC 688981.SH ↔ 0981.HK), an AH premium ratio (default 1.3×) is applied to the H-share consensus TP.

### 3.2 Buy Signal (Watchlist)

Purpose: Determine if current price presents a buying opportunity.

| Component | Weight | Source | Logic |
|-----------|--------|--------|-------|
| Research | 30% | logic_chains.db | Bullish signal strength + key drivers |
| Market Trend | 25% | Daily bars | Technical trend direction + volume confirmation |
| Price Proximity | 30% | User target price | How close current price is to desired entry |
| Valuation | 15% | Consensus TP | Upside to analyst consensus |

**Price Proximity Scale:**
| Ratio (price/target) | Score | Meaning |
|---------------------|-------|---------|
| ≤ 1.02 | 3.0 | At or below target — optimal entry |
| ≤ 1.05 | 2.4 | Very close |
| ≤ 1.10 | 1.5 | Within range |
| ≤ 1.20 | 0.5 | Approaching |
| > 1.20 | 0.0 | Too far |

**Thresholds:** score ≥ 4 → BUY_NOW, score ≥ 2 → WATCH, else WAIT.

### 3.3 Technical Score Components

| Indicator | Bullish | Neutral | Bearish |
|-----------|---------|---------|---------|
| MA Position | above_20_50: +1 | above_20: +0.5 | below: −1 |
| MACD | golden_cross: +2 | bullish: +1 | bearish: −1, death_cross: −2 |
| Regime | bullish: +2 | neutral: 0 | bearish: −2 |
| Volume | >1.5× & confirms trend: ±1 | — | — |

## 4. Data Layer

### 4.1 Price Sources (Priority Order)

```
A-share:  QMT bridge  →  Eastmoney MX  →  baostock (T+1 fallback)
US:       Finnhub     →  (yfinance backup not yet active)
HK:       yfinance (via SOCKS5 proxy)
```

### 4.2 Daily Bars (Technical Analysis)

```
A-share:  baostock (后复权, 80-day lookback)
US/HK:    yfinance history()
```

### 4.3 Research Signals

```
logic_chains.db  →  30-day window  →  company name / ticker matching
valuation.db     →  90-day window  →  consensus TP (IQR-filtered median)
```

### 4.4 Holdings Management

```
holdings.db (SQLite)
  ├── holdings    — SELL positions + BUY watchlist
  ├── alert_log   — historical signal records
  └── Dashboard   — /holdings page (CRUD)
```

## 5. Key Files

| File | Purpose |
|------|---------|
| `hermes/realtime_alert.py` | Main orchestrator: fetch → score → email |
| `hermes/signal_scorer.py` | Shared scoring engine (pure functions) |
| `hermes/bearish_alert.py` | Bearish signal scanner + email |
| `hermes/server.py` | Dashboard (line ~950-1270: /holdings page + /api/holdings CRUD) |
| `hermes/holdings.db` | Portfolio database |
| `hermes/config.json` | tracking.companies + other_account + realtime_alert config |
| `hermes/.launchd/*.plist` | Scheduled task definitions |
| `hermes/tests/test_scoring.py` | 21 unit tests for scoring functions |
| `hermes/tests/test_bearish_alert.py` | 4 integration tests for alert pipeline |
| `QuanTrading/config/settings.py` | QMT bridge connection + STOCK_SECTOR |

## 6. Email Format

```
📊 Holdings Signal — YYYY-MM-DD HH:MM
Session: [A-share Mid-Session | US Mid-Session | Manual Run]

Positions: N | Watchlist: M
SELL: X | BUY_NOW: Y | WATCH: Z

── 持仓预警 ──
标的                    现价     涨跌   信号  评分  关键指标
────────────────────────────────────────────────────
🔴 Broadcom           $372.10  -5.1%   BUY +11.2  bullish:16H/6M | MA:below...

── 买入监控 ──
标的                    现价    目标价   差距    信号     评分  关键指标
────────────────────────────────────────────────────
🟢 Apple              $291.58 270.00  +8.0% BUY_NOW +11.6  target:270 vs 292...

─────────────────────────────────────────
Portfolio P&L: -225,280 | 2026-06-11 16:30
Auto-generated by Hermes Realtime Alert.
```

## 7. Dedup Mechanism

Signals are deduplicated per-day to avoid email spam:

```
push_history.json
  └── {"type": "realtime_alert", "key": "md5(ticker|date|action)", ...}
```

Same ticker + same date + same action → skipped. Different action (e.g., HOLD → SELL later in day) → sent.

## 8. Graceful Degradation

| Failure | Behavior |
|---------|----------|
| QMT bridge down | Falls back to Eastmoney → baostock |
| Finnhub rate-limited | Price shows as "—", research + technicals still scored |
| yfinance rate-limited | Daily bars empty, technical score = 0, research score still valid |
| logic_chains.db missing | Research score = 0, technical-only scoring |
| Email config missing | Prints to console, does not crash |

## 9. Configuration

Key configurable parameters in `config.json` → `realtime_alert`:

```json
{
  "realtime_alert": {
    "enabled": true,
    "logic_chain_window_days": 30,
    "score_threshold_buy": 3,
    "score_threshold_sell": -3,
    "score_threshold_buy_now": 4,
    "score_threshold_watch": 2
  }
}
```

AH premium for cross-listed stocks is configured per-company in `tracking.companies[]`:

```json
{
  "name": "SMIC",
  "ticker": "0981.HK",
  "ah_cross": {
    "h_ticker": "0981.HK",
    "a_ticker": "688981.SH",
    "premium_ratio": 1.3
  }
}
```

## 10. Planned Enhancements

### Volume Depth Metrics (P2 — pending US/HK data source)

Add volume quality scoring to `signal_scorer.py`:

| Metric | A-share Source | US/HK Source | Status |
|--------|---------------|-------------|--------|
| Turnover rate (%) | baostock `turn` field | yfinance `sharesOutstanding` / volume | ⏳ pending |
| Volume-price correlation | Compare volume vs price direction (5d) | Same | ⏳ pending |
| Volume percentile | Historical 60d volume distribution | Same | ⏳ pending |

Proposed scoring logic:
- Turnover > 5% + price up → strong bullish confirmation (+2)
- Turnover > 5% + price down → distribution/panic (−2)
- Volume at 90th percentile + price up → institutional buying (+1)
- Volume at 90th percentile + price down → capitulation (−1)

Blocked by: US/HK real-time data source (AllTick or IB TWS API pending).

### Other Planned Items

| Item | Priority | Notes |
|------|----------|-------|
| Multi-day launchd schedule (Mon-Fri) | P2 | Currently single weekday due to plist limitation |
| Dashboard alert history view | P3 | `/holdings/<id>/alerts` API exists, needs UI |
| Weight calibration from backtest | P3 | Use QuanTrading backtest engine to optimize thresholds |
| Pre-market gap detection | P3 | Compare open vs previous close for gap alerts |

## 11. Testing

```bash
# Unit tests (scoring functions)
pytest tests/test_scoring.py -v

# Integration tests (bearish alert pipeline)
pytest tests/test_bearish_alert.py -v

# Dry-run (console preview)
python3 realtime_alert.py --market A --dry-run
python3 realtime_alert.py --market US --dry-run

# Full send (email)
python3 realtime_alert.py --market ALL
```
