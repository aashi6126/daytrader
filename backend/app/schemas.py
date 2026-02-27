from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from app.models import AlertStatus, ExitReason, TradeDirection, TradeEventType, TradeStatus


# --- Webhook ---


class TradingViewAlert(BaseModel):
    ticker: str = Field(..., description="Symbol, e.g. SPY")
    action: str = Field(..., description="BUY_CALL or BUY_PUT")
    secret: str = Field(..., description="Shared secret for auth")
    price: Optional[float] = Field(None, description="SPY price at signal time")
    comment: Optional[str] = None
    source: Optional[str] = Field(None, description="Alert origin: 'test' or omitted for TradingView")

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: str) -> str:
        allowed = {"BUY_CALL", "BUY_PUT", "CLOSE"}
        if v.upper() not in allowed:
            raise ValueError(f"action must be one of {allowed}")
        return v.upper()

    @property
    def direction(self) -> Optional[TradeDirection]:
        if self.action == "CLOSE":
            return None
        return TradeDirection.CALL if self.action == "BUY_CALL" else TradeDirection.PUT


class WebhookResponse(BaseModel):
    status: str
    message: str
    trade_id: Optional[int] = None


# --- Alert ---


class AlertResponse(BaseModel):
    id: int
    received_at: datetime
    ticker: str
    direction: Optional[TradeDirection]
    signal_price: Optional[float]
    source: Optional[str]
    status: AlertStatus
    rejection_reason: Optional[str]
    trade_id: Optional[int]
    raw_payload: Optional[str] = None

    model_config = {"from_attributes": True}


class AlertListResponse(BaseModel):
    alerts: list[AlertResponse]
    total: int
    page: int
    per_page: int


# --- Trade ---


class TradeResponse(BaseModel):
    id: int
    trade_date: date
    direction: TradeDirection
    option_symbol: str
    strike_price: float
    entry_price: Optional[float]
    entry_quantity: int
    entry_filled_at: Optional[datetime]
    alert_option_price: Optional[float] = None
    entry_is_fallback: Optional[bool] = None
    exit_price: Optional[float]
    exit_filled_at: Optional[datetime]
    exit_reason: Optional[ExitReason]
    stop_loss_price: Optional[float]
    trailing_stop_price: Optional[float]
    highest_price_seen: Optional[float]
    pnl_dollars: Optional[float]
    pnl_percent: Optional[float]
    status: TradeStatus
    source: Optional[str]
    created_at: datetime
    best_entry_price: Optional[float] = None
    best_entry_minutes: Optional[float] = None
    ticker: Optional[str] = None
    entry_regime: Optional[str] = None
    entry_regime_confidence: Optional[float] = None
    entry_vix: Optional[float] = None
    adapter_applied: Optional[bool] = None

    model_config = {"from_attributes": True}


class TradeListResponse(BaseModel):
    trades: list[TradeResponse]
    total: int
    page: int
    per_page: int


# --- Trade Events ---


class TradeEventResponse(BaseModel):
    id: int
    trade_id: int
    timestamp: datetime
    event_type: TradeEventType
    message: str
    details: Optional[dict] = None

    model_config = {"from_attributes": True}

    @field_validator("details", mode="before")
    @classmethod
    def parse_details_json(cls, v):
        if isinstance(v, str):
            import json
            return json.loads(v)
        return v


class TradeEventListResponse(BaseModel):
    events: list[TradeEventResponse]
    trade_id: int


# --- Price Snapshots ---


class PriceSnapshotResponse(BaseModel):
    timestamp: datetime
    price: float
    highest_price_seen: float

    model_config = {"from_attributes": True}


class PriceSnapshotListResponse(BaseModel):
    snapshots: list[PriceSnapshotResponse]
    trade_id: int
    entry_price: Optional[float]
    stop_loss_price: Optional[float]


# --- Dashboard ---


class DailyStatsResponse(BaseModel):
    trade_date: date
    total_trades: int
    trades_remaining: int
    winning_trades: int
    losing_trades: int
    total_pnl: float
    win_rate: float
    open_positions: int


class PnLDataPoint(BaseModel):
    timestamp: Optional[datetime]
    cumulative_pnl: float
    trade_id: int


class PnLChartResponse(BaseModel):
    data_points: list[PnLDataPoint]
    total_pnl: float


# --- PnL Summary (weekly/monthly) ---


class PnLSummaryDay(BaseModel):
    trade_date: date
    pnl: float
    total_trades: int
    winning_trades: int
    losing_trades: int


class PnLSummaryResponse(BaseModel):
    period: str
    days: list[PnLSummaryDay]
    total_pnl: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float


# --- Strategy ---


class EnableStrategyRequest(BaseModel):
    ticker: str
    timeframe: str
    signal_type: str
    params: dict


class DisableStrategyRequest(BaseModel):
    ticker: str
    timeframe: str
    signal_type: str


class EnabledStrategyEntry(BaseModel):
    ticker: str
    timeframe: str
    signal_type: str
    params: Optional[dict] = None
    enabled_at: Optional[str] = None


class EnabledStrategiesResponse(BaseModel):
    strategies: List[EnabledStrategyEntry] = []


# --- WebSocket ---


class WSMessage(BaseModel):
    event: str
    data: dict
