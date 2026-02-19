from __future__ import annotations

from typing import List, Optional

from pydantic_settings import BaseSettings


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
    MAX_DAILY_LOSS: float = 500.0
    DEFAULT_QUANTITY: int = 2
    STOP_LOSS_PERCENT: float = 16.0
    TRADE_COOLDOWN_MINUTES: int = 5
    SIGNAL_DEBOUNCE_MINUTES: int = 2
    DEDUP_WINDOW_SECONDS: int = 30
    MIN_PRICE_RANGE: float = 0.50
    MAX_CONSECUTIVE_LOSSES: int = 3

    # Entry limit strategy
    ENTRY_LIMIT_BELOW_PERCENT: float = 5.0
    ENTRY_LIMIT_TIMEOUT_MINUTES: float = 3.0

    # Exit Strategy
    PROFIT_TARGET_PERCENT: float = 40.0
    TRAILING_STOP_PERCENT: float = 20.0
    TRAILING_STOP_AFTER_SCALE_OUT_PERCENT: float = 10.0
    MAX_HOLD_MINUTES: int = 90
    FORCE_EXIT_HOUR: int = 15
    FORCE_EXIT_MINUTE: int = 30
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

    # Monitoring Intervals
    ORDER_POLL_INTERVAL_SECONDS: int = 5
    EXIT_CHECK_INTERVAL_SECONDS: int = 10

    # ORB Auto Strategy
    ACTIVE_STRATEGY: str = "orb_auto"  # "orb_auto" | "tradingview" | "disabled"
    ORB_MIN_RANGE: float = 0.30
    ORB_POLL_INTERVAL_SECONDS: int = 30

    # Data Recorder (for backtesting)
    DATA_RECORDER_ENABLED: bool = False
    DATA_RECORDER_INTERVAL_SECONDS: int = 60
    DATA_RECORDER_STRIKE_COUNT: int = 20

    # Frontend
    CORS_ORIGINS: List[str] = ["http://localhost:5173"]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}
