# DayTrader 0DTE — Application Behavior Document

## Overview

DayTrader is an automated 0DTE (zero days to expiration) SPY options trading system. It receives buy/sell signals from TradingView via webhooks, selects the optimal option contract using real-time Schwab market data, places orders through the Schwab API, and manages the full trade lifecycle — entry, stop-loss, profit target, trailing stop, and forced exit.

A React dashboard provides real-time monitoring via WebSocket updates.

---

## System Architecture

```
TradingView Alert
       │
       ▼
  POST /api/webhook  (FastAPI)
       │
       ├─ Validate secret, ticker, daily limit
       ├─ Log raw payload to alerts.log + DB
       │
       ▼
  TradeManager.process_alert()
       │
       ├─ OptionSelector.select_contract()
       │     └─ Schwab API: GET option chain → pick best 0DTE by delta
       │
       ├─ SchwabService.place_order()  (LIMIT BUY_TO_OPEN at ask)
       │
       └─ Save Trade (status=PENDING), broadcast via WebSocket
              │
              ▼
     ┌─ OrderMonitorTask (every 5s) ─┐
     │  Polls Schwab order status     │
     │  PENDING → FILLED:             │
     │    • Record entry_price         │
     │    • Place stop-loss order      │
     │    • Status → STOP_LOSS_PLACED  │
     └────────────────────────────────┘
              │
              ▼
     ┌─ ExitMonitorTask (every 10s) ─┐
     │  For each open trade:          │
     │  1. Force exit at 3:30 PM ET   │
     │  2. Max hold time (180 min)    │
     │  3. Stop-loss (−40%)           │
     │  4. Profit target (+40%)       │
     │  5. Trailing stop (15% drop)   │
     │  → Places SELL_TO_CLOSE order  │
     │  → Status → EXITING            │
     └────────────────────────────────┘
              │
              ▼
     ┌─ OrderMonitorTask (every 5s) ─┐
     │  EXITING → CLOSED:             │
     │    • Record exit_price          │
     │    • Calculate P&L              │
     │    • Broadcast trade_closed     │
     └────────────────────────────────┘
              │
              ▼
     ┌─ EODCleanupTask (4:05 PM ET) ─┐
     │  Compute daily summary:         │
     │  win rate, total P&L, etc.      │
     └────────────────────────────────┘
```

---

## Trade Lifecycle

### States

| Status | Meaning |
|---|---|
| `PENDING` | Entry order placed, waiting for fill |
| `FILLED` | Entry filled, stop-loss not yet placed |
| `STOP_LOSS_PLACED` | Active position with stop-loss order on Schwab |
| `EXITING` | Exit order placed, waiting for fill |
| `CLOSED` | Exit filled, P&L calculated |
| `CANCELLED` | Entry order was cancelled/rejected/expired |
| `ERROR` | Unrecoverable error during processing |

### State Transitions

```
PENDING ──fill──→ FILLED ──stop placed──→ STOP_LOSS_PLACED ──exit triggered──→ EXITING ──fill──→ CLOSED
   │                                            │
   └──cancel/reject──→ CANCELLED                └──stop hit on Schwab──→ CLOSED
```

---

## Webhook Endpoint

**POST `/api/webhook`**

### Request Format (from TradingView)

```json
{
  "secret": "YoGnOKFZBJBd4ZuxXrLVrHu2bY8J8BfE",
  "ticker": "SPY",
  "action": "BUY_CALL",
  "price": 694.50,
  "comment": "RSI crossover",
  "source": "tradingview"
}
```

- `action`: One of `BUY_CALL`, `BUY_PUT`, `CLOSE`
- `price`: Optional — SPY price at signal time (used as fallback only)
- `comment`, `source`: Optional metadata
- Content-Type can be `text/plain` (TradingView default) or `application/json`

### Processing Flow

1. **Parse**: Read raw body, decode as JSON (handles `text/plain` from TradingView)
2. **Log**: Write raw payload to `backend/logs/alerts.log` and save to `alerts` DB table
3. **Authenticate**: Compare `secret` field to `WEBHOOK_SECRET` env var → 401 if mismatch
4. **Validate ticker**: Only `SPY` is accepted → reject otherwise
5. **Route by action**:
   - `BUY_CALL` / `BUY_PUT` → `TradeManager.process_alert()`
   - `CLOSE` → `TradeManager.close_open_position()`

### Rejection Reasons

| Reason | HTTP Status |
|---|---|
| Invalid JSON | 400 |
| Pydantic validation error | 422 |
| Wrong secret | 401 |
| Unsupported ticker | 200 (rejected) |
| Daily trade limit reached | 200 (rejected) |
| No open positions (CLOSE) | 200 (rejected) |
| No 0DTE contracts found | 500 |
| No suitable contract (illiquid) | 500 |

---

## Option Selection

When a `BUY_CALL` or `BUY_PUT` signal arrives, the system selects the best 0DTE option contract:

1. **Fetch option chain** from Schwab API:
   - Symbol: SPY
   - Contract type: CALL or PUT (from alert action)
   - Date range: today only (0DTE)
   - Strike count: 20 (returns 20 strikes above and below ATM)

2. **Filter to today's expiration** — match `date.today()` against chain expiration keys

3. **Score each contract**:
   - Skip if bid ≤ 0 or ask ≤ 0 (no market)
   - Skip if spread % > `OPTION_MAX_SPREAD_PERCENT` (default 10%)
   - Score = |delta − `OPTION_DELTA_TARGET`| + (spread% / 100)
   - Lower score = better contract

4. **Select the contract with the lowest score** — closest to target delta (0.45) with tightest spread

### Example Selection

```
SPY at $693.91, BUY_CALL signal:
  Strikes returned: 684-703
  Selected: SPY 260209C00694000
    strike=694, delta=0.48, bid=$0.41, ask=$0.42, spread=2.4%
```

---

## Order Payloads Sent to Schwab

### Entry Order (BUY_TO_OPEN)

Placed at the **ask price** for fast fill:

```json
{
  "orderType": "LIMIT",
  "session": "NORMAL",
  "duration": "DAY",
  "orderStrategyType": "SINGLE",
  "price": "0.42",
  "orderLegCollection": [
    {
      "instruction": "BUY_TO_OPEN",
      "quantity": 1,
      "instrument": {
        "symbol": "SPY   260209C00694000",
        "assetType": "OPTION"
      }
    }
  ]
}
```

### Stop-Loss Order

Placed after entry fill at `entry_price × (1 − STOP_LOSS_PERCENT / 100)`:

```json
{
  "orderType": "STOP",
  "session": "NORMAL",
  "duration": "DAY",
  "orderStrategyType": "SINGLE",
  "stopPrice": "0.25",
  "orderLegCollection": [
    {
      "instruction": "SELL_TO_CLOSE",
      "quantity": 1,
      "instrument": {
        "symbol": "SPY   260209C00694000",
        "assetType": "OPTION"
      }
    }
  ]
}
```

### Exit Order (SELL_TO_CLOSE)

Placed as MARKET when exit conditions are met:

```json
{
  "orderType": "MARKET",
  "session": "NORMAL",
  "duration": "DAY",
  "orderStrategyType": "SINGLE",
  "orderLegCollection": [
    {
      "instruction": "SELL_TO_CLOSE",
      "quantity": 1,
      "instrument": {
        "symbol": "SPY   260209C00694000",
        "assetType": "OPTION"
      }
    }
  ]
}
```

---

## Exit Conditions (Priority Order)

The ExitMonitorTask evaluates open trades every 10 seconds. Conditions are checked in this order — the first one that triggers wins:

| Priority | Condition | Exit Reason | Logic |
|---|---|---|---|
| 1 | **Force Exit** | `TIME_BASED` | Current time ≥ 3:30 PM ET (`FORCE_EXIT_HOUR:FORCE_EXIT_MINUTE`) |
| 2 | **Max Hold Time** | `MAX_HOLD_TIME` | Minutes since entry fill ≥ `MAX_HOLD_MINUTES` (180) |
| 3 | **App-Managed Stop** | `STOP_LOSS` | Current price ≤ stop_loss_price AND no Schwab stop order active |
| 4 | **Profit Target** | `PROFIT_TARGET` | Current price ≥ entry_price × (1 + `PROFIT_TARGET_PERCENT` / 100) |
| 5 | **Trailing Stop** | `TRAILING_STOP` | Current price ≤ highest_price_seen × (1 − `TRAILING_STOP_PERCENT` / 100) |

The trailing stop continuously updates `highest_price_seen` and `trailing_stop_price` as the option price moves higher.

When triggered, the existing stop-loss order on Schwab is cancelled before placing the exit order.

---

## CLOSE Action

When a `CLOSE` webhook arrives:

1. Find the most recent trade with status `FILLED` or `STOP_LOSS_PLACED`
2. If no open position → reject with "No open positions to close"
3. Cancel the stop-loss order on Schwab (if present)
4. Place a MARKET SELL_TO_CLOSE order
5. Set status to `EXITING`, exit_reason to `SIGNAL`
6. OrderMonitorTask detects the fill and calculates P&L

---

## Operating Modes

### 1. Live Mode (`PAPER_TRADE=false`, `DRY_RUN=false`)

- Real Schwab API for everything: option chains, quotes, order placement
- Orders execute on the Schwab account specified by `SCHWAB_ACCOUNT_HASH`
- Requires valid OAuth2 tokens (refresh every 7 days via `scripts/auth_setup`)

### 2. Dry Run Mode (`PAPER_TRADE=false`, `DRY_RUN=true`)

- **Real** Schwab API for option chains and quotes (real market data)
- Orders are **logged but not placed** — full payload written to stdout
- Simulates immediate fills at the limit/stop price
- Useful for verifying contract selection and order payloads before going live

### 3. Paper Trade Mode (`PAPER_TRADE=true`)

- **Mock** Schwab client (`PaperSchwabClient`) — no Schwab API calls at all
- Returns hardcoded option chain data (not real market prices)
- BUY/SELL orders auto-fill instantly at the limit price
- STOP orders remain WORKING (exit engine evaluates them)
- Useful for testing the full trade lifecycle without any API dependency

---

## Background Tasks

| Task | Interval | Purpose |
|---|---|---|
| `OrderMonitorTask` | 5 seconds | Polls Schwab for entry/exit/stop-loss fill status |
| `ExitMonitorTask` | 10 seconds | Evaluates exit conditions, places exit orders |
| `EODCleanupTask` | Once at 4:05 PM ET | Computes daily summary (win rate, P&L, etc.) |

All tasks run as async background coroutines started during FastAPI lifespan.

---

## Database Schema

### alerts

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | Auto-increment |
| received_at | DATETIME | UTC timestamp |
| raw_payload | TEXT | Full webhook body |
| ticker | VARCHAR(10) | Always "SPY" |
| direction | ENUM | CALL, PUT, or NULL (for CLOSE) |
| signal_price | FLOAT | Price from alert |
| source | VARCHAR(20) | "tradingview" or "test" |
| status | ENUM | RECEIVED → PROCESSED/REJECTED/ERROR |
| rejection_reason | VARCHAR(255) | Why rejected (if applicable) |
| trade_id | INTEGER FK | Linked trade (if processed) |

### trades

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | Auto-increment |
| trade_date | DATE | Trading day |
| direction | ENUM | CALL or PUT |
| option_symbol | VARCHAR(50) | e.g., "SPY   260209C00694000" |
| strike_price | FLOAT | e.g., 694.0 |
| expiration_date | DATE | Always today (0DTE) |
| entry_order_id | VARCHAR(50) | Schwab order ID |
| entry_price | FLOAT | Fill price |
| entry_quantity | INTEGER | Number of contracts |
| entry_filled_at | DATETIME | UTC |
| exit_order_id | VARCHAR(50) | Schwab order ID |
| exit_price | FLOAT | Fill price |
| exit_filled_at | DATETIME | UTC |
| exit_reason | ENUM | STOP_LOSS, PROFIT_TARGET, etc. |
| stop_loss_order_id | VARCHAR(50) | Schwab stop order ID |
| stop_loss_price | FLOAT | Trigger price |
| trailing_stop_price | FLOAT | Dynamic trailing price |
| highest_price_seen | FLOAT | High-water mark for trailing stop |
| pnl_dollars | FLOAT | (exit − entry) × quantity × 100 |
| pnl_percent | FLOAT | (exit − entry) / entry × 100 |
| status | ENUM | PENDING → ... → CLOSED |
| source | VARCHAR(20) | "tradingview" or "test" |
| created_at | DATETIME | UTC |
| updated_at | DATETIME | UTC, auto-updated |

### daily_summaries

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | Auto-increment |
| trade_date | DATE | Unique |
| total_trades | INTEGER | Non-cancelled count |
| winning_trades | INTEGER | P&L > 0 |
| losing_trades | INTEGER | P&L ≤ 0 |
| total_pnl | FLOAT | Sum of all P&L |
| largest_win | FLOAT | Best single trade |
| largest_loss | FLOAT | Worst single trade |
| win_rate | FLOAT | winners / closed × 100 |
| avg_hold_time_minutes | FLOAT | Mean hold duration |

---

## Frontend

### Pages

| Route | Page | Purpose |
|---|---|---|
| `/` | Dashboard | Real-time stats, P&L chart, open positions |
| `/history` | Trade History | Paginated list of all trades with filters |
| `/alerts` | Alerts History | Paginated list of all alerts with expandable raw payload |
| `/testing` | Testing | Manual alert sending and trade closing |

### Real-Time Updates

The Dashboard connects via WebSocket to `ws://localhost:8000/api/ws/dashboard`. Events:

| Event | Trigger | Dashboard Action |
|---|---|---|
| `trade_created` | New entry order placed | Refresh stats + open positions |
| `trade_filled` | Entry order filled | Refresh stats + open positions |
| `trade_closed` | Exit order filled | Refresh stats + P&L chart + open positions |
| `trade_cancelled` | Entry order cancelled | Refresh stats |

Auto-reconnects every 3 seconds on disconnect. Dashboard also polls every 30 seconds as fallback.

### Nav Bar

- Navigation links: Dashboard, Trade History, Alerts, Testing
- Paper/Live toggle: Shows current mode badge (yellow=Paper, green=Live)
  - Toggle updates `.env` file but requires server restart to take effect

### Timezone Display

All timestamps are stored as naive UTC in the database. The frontend appends `Z` suffix before converting to `America/New_York` (Eastern Time) for display.

---

## Configuration Reference

All settings are in `backend/.env`:

```ini
# Webhook Authentication
WEBHOOK_SECRET=your-secret-here

# Schwab API Credentials
SCHWAB_APP_KEY=your-app-key
SCHWAB_APP_SECRET=your-app-secret
SCHWAB_CALLBACK_URL=https://127.0.0.1
SCHWAB_TOKENS_DB=~/.schwabdev/tokens.db
SCHWAB_ACCOUNT_HASH=your-account-hash

# Trading Parameters
MAX_DAILY_TRADES=10          # Max trades per day
DEFAULT_QUANTITY=1           # Contracts per trade
STOP_LOSS_PERCENT=40.0       # Stop-loss % below entry
PROFIT_TARGET_PERCENT=40.0   # Profit target % above entry
TRAILING_STOP_PERCENT=15.0   # Trailing stop % from peak
MAX_HOLD_MINUTES=180         # Max hold time before forced exit
FORCE_EXIT_HOUR=15           # Force exit hour (ET)
FORCE_EXIT_MINUTE=30         # Force exit minute (ET)

# Option Selection
OPTION_DELTA_TARGET=0.45     # Target delta for contract selection
OPTION_MAX_SPREAD_PERCENT=10.0  # Max bid-ask spread %

# Monitoring
ORDER_POLL_INTERVAL_SECONDS=5   # Order status check interval
EXIT_CHECK_INTERVAL_SECONDS=10  # Exit condition check interval

# Mode
PAPER_TRADE=false            # true=mock client, false=real Schwab API
DRY_RUN=true                 # true=log orders without placing, false=place real orders
LOG_LEVEL=INFO
```

---

## Running the Application

### Backend

```bash
cd backend
python -m scripts.auth_setup    # First-time: Schwab OAuth2 login
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Frontend

```bash
cd frontend
npm run dev                     # Starts on http://localhost:5173
```

### Logs

```bash
tail -f /tmp/daytrader.log              # Backend stdout (if redirected)
cat backend/logs/alerts.log             # Raw webhook payloads
```

### OAuth Token Refresh

Schwab refresh tokens expire every 7 days. Re-run:

```bash
cd backend && .venv/bin/python -m scripts.auth_setup
```
