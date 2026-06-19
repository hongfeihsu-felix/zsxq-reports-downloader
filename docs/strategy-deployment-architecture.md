# Strategy Co-Location Architecture

## Problem

Current architecture has strategy execution on Mac, crossing the network twice per trade:
```
MiniQMT → WS → Mac (strategy calc) → HTTP → MiniQMT (order)
                 ↑ 50-200ms latency          ↑ network risk
```

## Solution

Strategy executes on Windows server, co-located with MiniQMT. Mac handles config, monitoring, and alerts.

## Architecture

```
┌─ Windows Cloud Server ─────────────────────────────────────┐
│                                                              │
│  MiniQMT ──bars──→ bridge/server.py                          │
│                      │                                       │
│                      ├── StrategyRunner (NEW)                │
│                      │   ├── subscribe("600183.SH", "5min")  │
│                      │   ├── on_bar() → MACD/ATR strategies  │
│                      │   └── place_order() → MiniQMT         │
│                      │                                       │
│                      ├── HTTP API (existing)                 │
│                      │   ├── GET /positions                  │
│                      │   ├── GET /health                     │
│                      │   ├── POST /strategy/deploy (NEW)     │
│                      │   └── GET /strategy/status  (NEW)     │
│                      │                                       │
│                      └── WebSocket (existing)                 │
│                          ├── bar push (downstream)            │
│                          ├── trade confirm (upstream)  (NEW) │
│                          └── P&L update (upstream)     (NEW) │
│                                                              │
└──────────────────────────────────────────────────────────────┘
         │                        │
         │ HTTP (deploy/config)   │ WebSocket (monitor)
         ▼                        ▼
┌─ Mac ────────────────────────────────────────────────────────┐
│                                                               │
│  scripts/deploy_strategy.py                                   │
│    ├── reads config/settings.py                               │
│    ├── POST /strategy/deploy → Windows                        │
│    └── launchd: 9:20 AM daily (before market open)            │
│                                                               │
│  Hermes Dashboard                                              │
│    ├── /paper → paper trading status                          │
│    └── /holdings → real-time P&L via WS                       │
│                                                               │
│  Monitoring (via existing WS connection)                      │
│    ├── Trade confirmations pushed from Windows                │
│    └── P&L updates every bar                                  │
│                                                               │
└───────────────────────────────────────────────────────────────┘
```

## Components

### 1. StrategyRunner (bridge/server.py, ~120 lines)

New class inside the existing QMT bridge server:

```python
class StrategyRunner:
    def __init__(self, bridge: QMTBridge):
        self._bridge = bridge
        self._strategies: dict[str, list] = {}  # symbol → [strategy]
        self._active = False

    def deploy(self, config: dict):
        """POST /strategy/deploy
        config = {
            "mode": "paper" | "live",
            "symbols": ["600183.SH", ...],
            "strategies": [
                {"name": "macd_signal", "params": {...}, "weight": 0.5},
                {"name": "atr_grid", "params": {...}, "weight": 0.5}
            ],
            "base_shares": {"600183.SH": 500, ...},
            "initial_cash": 1000000,
            "risk": {"max_daily_loss_pct": -0.03, ...}
        }
        """
        self._config = config
        # Subscribe to symbols
        self._bridge.subscribe(config["symbols"], callback=self._on_bar)

    def _on_bar(self, bar: dict):
        # 1. Run all strategies
        # 2. Aggregate signals
        # 3. Risk check
        # 4. Place order via MiniQMT (paper or live)
        # 5. Push trade confirmation via WS
        pass

    def status(self) -> dict:
        return {"active": self._active, "symbols": [...], "pnl": ...}
```

### 2. Deploy Script (scripts/deploy_strategy.py, ~60 lines)

```python
#!/usr/bin/env python3
"""Deploy strategy config to Windows QMT bridge before market open."""
import json, sys, requests

BRIDGE_HOST = "124.220.26.164"
BRIDGE_HTTP_PORT = 8766

# Read strategy config from file or command line
config_file = sys.argv[1] if len(sys.argv) > 1 else "config/paper_strategy.json"

with open(config_file) as f:
    config = json.load(f)

# Deploy to Windows
resp = requests.post(
    f"http://{BRIDGE_HOST}:{BRIDGE_HTTP_PORT}/strategy/deploy",
    json=config, timeout=10
)
print(resp.json())
```

### 3. Strategy Config File (config/paper_strategy.json, ~40 lines)

```json
{
  "mode": "paper",
  "symbols": ["600183.SH", "002436.SZ", "002409.SZ", "688072.SH", "688981.SH", "300408.SZ"],
  "strategies": [
    {"name": "macd_signal", "params": {}, "weight": 0.5},
    {"name": "atr_grid", "params": {}, "weight": 0.5}
  ],
  "base_shares": {"600183.SH": 500, "002436.SZ": 1500, "002409.SZ": 450, "688072.SH": 200, "688981.SH": 600, "300408.SZ": 1500},
  "initial_cash": 1000000,
  "risk": {"max_daily_loss_pct": -0.03, "max_consecutive_losses": 3}
}
```

### 4. Launchd (auto-deploy 5 min before market open)

```xml
<!-- com.daytrader.deploy-strategy.plist -->
<key>StartCalendarInterval</key>
<dict>
    <key>Hour</key><integer>9</integer>
    <key>Minute</key><integer>20</integer>
</dict>
<key>ProgramArguments</key>
<array>
    <string>/Users/hongfeihsu/ClaudeCode/QuanTrading/venv/bin/python3</string>
    <string>/Users/hongfeihsu/ClaudeCode/QuanTrading/scripts/deploy_strategy.py</string>
</array>
```

## Data Flow (One Trade, Post-Refactor)

```
09:30:00  MiniQMT emits 5-min bar for 600183.SH
09:30:00  bridge/server.py StrategyRunner._on_bar() fires
09:30:00  MACD strategy runs (0-5ms, local)
09:30:00  Signal: BUY 600183.SH qty=500 score=0.6
09:30:00  Risk check passes
09:30:00  MiniQMT.place_order() called (local, no network)
          → paper mode: simulated fill
          → live mode: real order to exchange
09:30:01  Trade confirmation pushed to Mac via WS
09:30:01  Dashboard updates P&L in real-time
```

Total latency: <10ms (vs 50-200ms before).

## Implementation Order

1. Add `StrategyRunner` class to `bridge/server.py`
2. Add `POST /strategy/deploy` and `GET /strategy/status` endpoints
3. Create `config/paper_strategy.json` config file
4. Create `scripts/deploy_strategy.py` deploy script
5. Create launchd plist for auto-deploy at 9:20 AM
6. Test: deploy paper strategy → verify trades execute on Windows
7. Update Dashboard /paper page to show WS-pushed P&L
