from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from pydantic_settings import BaseSettings

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    # Application
    APP_NAME: str = "DayTrader 0DTE"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"
    PAPER_TRADE: bool = False
    DRY_RUN: bool = True

    # Database
    DATABASE_URL: str = "sqlite:///./daytrader.db"

    # TradingView Webhook
    WEBHOOK_SECRET: str = "change-me"

    # Schwab API (OAuth2 Authorization Code Flow)
    SCHWAB_APP_KEY: str = "change-me"
    SCHWAB_APP_SECRET: str = "change-me"
    SCHWAB_CALLBACK_URL: str = "https://127.0.0.1"
    SCHWAB_TOKENS_DB: str = "~/.schwabdev/tokens.db"
    SCHWAB_ACCOUNT_HASH: Optional[str] = None

    # Trading Parameters
    MAX_DAILY_TRADES: int = 10
    MAX_DAILY_LOSS: float = 700.0
    DEFAULT_QUANTITY: int = 2
    STOP_LOSS_PERCENT: float = 60.0
    TRADE_COOLDOWN_MINUTES: int = 5
    SIGNAL_DEBOUNCE_MINUTES: int = 2
    DEDUP_WINDOW_SECONDS: int = 30
    MIN_PRICE_RANGE: float = 0.50
    MAX_CONSECUTIVE_LOSSES: int = 3

    # Entry limit strategy
    ENTRY_LIMIT_BELOW_PERCENT: float = 5.0
    ENTRY_LIMIT_TIMEOUT_MINUTES: float = 3.0

    # Entry Filters
    MIN_OPTION_PRICE: float = 1.00  # Reject contracts with mid < this
    MIN_STOP_SPREAD_RATIO: float = 2.0  # Require stop_distance >= N× bid-ask spread

    # ATR-Based Stops
    ATR_STOP_ENABLED: bool = True
    ATR_PERIOD_DEFAULT: int = 14
    ATR_STOP_MULT_DEFAULT: float = 2.0

    # Position Sizing
    MAX_RISK_PER_TRADE: float = 300.0  # Max dollars at risk per trade

    # Entry Confirmation Delay
    ENTRY_CONFIRM_SECONDS: int = 10  # Minimum seconds after fill before placing stop
    ENTRY_CONFIRM_EMERGENCY_PCT: float = 15.0  # Emergency exit during confirmation
    ENTRY_CONFIRM_FAVORABLE_TICK: bool = True  # Require price to tick above entry before placing stop

    # 1-Minute Bar Confirmation (pre-entry filter)
    ENTRY_CONFIRM_1M: bool = True  # Require 1m bar to confirm direction before entering

    # Exit Strategy
    PROFIT_TARGET_PERCENT: float = 40.0
    TRAILING_STOP_PERCENT: float = 20.0
    TRAILING_STOP_ACTIVATION_PERCENT: float = 15.0  # Only start trailing after this % gain
    TRAILING_STOP_AFTER_SCALE_OUT_PERCENT: float = 10.0
    MAX_HOLD_MINUTES: int = 90
    FORCE_EXIT_HOUR: int = 15
    FORCE_EXIT_MINUTE: int = 30
    FIRST_ENTRY_HOUR: int = 10
    FIRST_ENTRY_MINUTE: int = 0
    LAST_ENTRY_HOUR: int = 14
    LAST_ENTRY_MINUTE: int = 45
    AFTERNOON_WINDOW_ENABLED: bool = True
    SCALE_OUT_ENABLED: bool = True
    BREAKEVEN_TRIGGER_PERCENT: float = 10.0
    SCALE_OUT_TIER_1_PERCENT: float = 20.0
    SCALE_OUT_TIER_1_QTY: int = 10
    SCALE_OUT_TIER_2_PERCENT: float = 40.0
    SCALE_OUT_TIER_2_QTY: int = 8

    # Option Selection
    OPTION_DELTA_TARGET: float = 0.4
    OPTION_MAX_SPREAD_PERCENT: float = 10.0

    # Dynamic Delta Selection
    DYNAMIC_DELTA_ENABLED: bool = True

    # IV Rank Filter — reject trades when options are too expensive
    IV_RANK_MAX: float = 70.0  # Skip trade when IV rank >= this (0-100 scale)

    # Strategy Adapter — dynamic stop/target/trailing based on regime + VIX
    STRATEGY_ADAPTER_ENABLED: bool = True

    # Spread-aware exit management
    EXIT_MAX_SPREAD_PERCENT: float = 30.0  # Skip trailing stop when spread > this % of mid

    # Monitoring Intervals
    ORDER_POLL_INTERVAL_SECONDS: int = 5
    EXIT_CHECK_INTERVAL_SECONDS: int = 10

    # Schwab Streaming (WebSocket)
    STREAMING_ENABLED: bool = True
    STREAMING_STALE_SECONDS: float = 30.0
    SNAPSHOT_RECORD_INTERVAL_SECONDS: float = 2.0  # How often PriceRecorderTask polls streaming cache

    # ORB Auto Strategy
    ACTIVE_STRATEGY: str = "orb_auto"  # "orb_auto" | "tradingview" | "disabled"
    # Allowed signal types for live trading (backtest can still test all)
    ALLOWED_LIVE_SIGNAL_TYPES: List[str] = ["orb", "orb_direction", "confluence"]
    ORB_MIN_RANGE: float = 0.30
    ORB_POLL_INTERVAL_SECONDS: int = 30

    # Data Recorder (for backtesting)
    DATA_RECORDER_ENABLED: bool = False
    DATA_RECORDER_INTERVAL_SECONDS: int = 60
    DATA_RECORDER_STRIKE_COUNT: int = 20

    # AI Assistant (Ollama — local)
    OLLAMA_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3.1:8b"

    # Frontend
    CORS_ORIGINS: List[str] = ["http://localhost:5173"]

    model_config = {"env_file": str(_ENV_FILE), "env_file_encoding": "utf-8"}
