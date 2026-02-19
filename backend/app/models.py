import enum
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class AlertStatus(str, enum.Enum):
    RECEIVED = "RECEIVED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    PROCESSED = "PROCESSED"
    ERROR = "ERROR"


class TradeStatus(str, enum.Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    STOP_LOSS_PLACED = "STOP_LOSS_PLACED"
    EXITING = "EXITING"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"
    ERROR = "ERROR"


class TradeDirection(str, enum.Enum):
    CALL = "CALL"
    PUT = "PUT"


class ExitReason(str, enum.Enum):
    STOP_LOSS = "STOP_LOSS"
    TRAILING_STOP = "TRAILING_STOP"
    PROFIT_TARGET = "PROFIT_TARGET"
    MAX_HOLD_TIME = "MAX_HOLD_TIME"
    TIME_BASED = "TIME_BASED"
    MANUAL = "MANUAL"
    SIGNAL = "SIGNAL"
    EXPIRY = "EXPIRY"


class TradeEventType(str, enum.Enum):
    ALERT_RECEIVED = "ALERT_RECEIVED"
    CONTRACT_SELECTED = "CONTRACT_SELECTED"
    ENTRY_ORDER_PLACED = "ENTRY_ORDER_PLACED"
    ENTRY_FILLED = "ENTRY_FILLED"
    ENTRY_CANCELLED = "ENTRY_CANCELLED"
    STOP_LOSS_PLACED = "STOP_LOSS_PLACED"
    STOP_LOSS_CANCELLED = "STOP_LOSS_CANCELLED"
    EXIT_TRIGGERED = "EXIT_TRIGGERED"
    EXIT_ORDER_PLACED = "EXIT_ORDER_PLACED"
    EXIT_FILLED = "EXIT_FILLED"
    STOP_LOSS_HIT = "STOP_LOSS_HIT"
    CLOSE_SIGNAL = "CLOSE_SIGNAL"
    MANUAL_CLOSE = "MANUAL_CLOSE"
    SCALE_OUT = "SCALE_OUT"
    BREAKEVEN_STOP_MOVED = "BREAKEVEN_STOP_MOVED"
    ENTRY_LIMIT_TIMEOUT = "ENTRY_LIMIT_TIMEOUT"


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    received_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    raw_payload = Column(Text, nullable=False)
    ticker = Column(String(10), nullable=False)
    direction = Column(Enum(TradeDirection), nullable=True)
    signal_price = Column(Float, nullable=True)
    source = Column(String(20), nullable=True)
    status = Column(Enum(AlertStatus), default=AlertStatus.RECEIVED)
    rejection_reason = Column(String(255), nullable=True)
    trade_id = Column(Integer, ForeignKey("trades.id"), nullable=True)

    trade = relationship("Trade", back_populates="alert")


class Trade(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(Date, nullable=False)
    direction = Column(Enum(TradeDirection), nullable=False)
    option_symbol = Column(String(30), nullable=False)
    strike_price = Column(Float, nullable=False)
    expiration_date = Column(Date, nullable=False)

    # Entry
    entry_order_id = Column(String(50), nullable=True)
    entry_price = Column(Float, nullable=True)
    entry_quantity = Column(Integer, nullable=False, default=1)
    entry_filled_at = Column(DateTime, nullable=True)
    alert_option_price = Column(Float, nullable=True)
    entry_is_fallback = Column(Boolean, default=False)

    # Stop-loss
    stop_loss_order_id = Column(String(50), nullable=True)
    stop_loss_price = Column(Float, nullable=True)

    # Trailing stop tracking
    trailing_stop_price = Column(Float, nullable=True)
    highest_price_seen = Column(Float, nullable=True)

    # Breakeven stop
    breakeven_stop_applied = Column(Boolean, default=False)

    # Scale-out (partial exit at profit target)
    scaled_out = Column(Boolean, default=False)
    scaled_out_quantity = Column(Integer, default=0)
    scaled_out_price = Column(Float, nullable=True)
    scaled_out_order_id = Column(String(50), nullable=True)
    scale_out_count = Column(Integer, default=0)

    # Exit
    exit_order_id = Column(String(50), nullable=True)
    exit_price = Column(Float, nullable=True)
    exit_filled_at = Column(DateTime, nullable=True)
    exit_reason = Column(Enum(ExitReason), nullable=True)

    # PnL
    pnl_dollars = Column(Float, nullable=True)
    pnl_percent = Column(Float, nullable=True)
    status = Column(Enum(TradeStatus), default=TradeStatus.PENDING)
    source = Column(String(20), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    alert = relationship("Alert", back_populates="trade", uselist=False)
    events = relationship("TradeEvent", back_populates="trade", order_by="TradeEvent.timestamp")


class TradeEvent(Base):
    __tablename__ = "trade_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_id = Column(Integer, ForeignKey("trades.id"), nullable=False, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    event_type = Column(Enum(TradeEventType), nullable=False)
    message = Column(String(500), nullable=False)
    details = Column(Text, nullable=True)

    trade = relationship("Trade", back_populates="events")


class TradePriceSnapshot(Base):
    __tablename__ = "trade_price_snapshots"
    __table_args__ = (
        Index("ix_price_snap_trade_time", "trade_id", "timestamp"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_id = Column(Integer, ForeignKey("trades.id"), nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    price = Column(Float, nullable=False)
    highest_price_seen = Column(Float, nullable=False)


class DailySummary(Base):
    __tablename__ = "daily_summaries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(Date, unique=True, nullable=False)
    total_trades = Column(Integer, default=0)
    winning_trades = Column(Integer, default=0)
    losing_trades = Column(Integer, default=0)
    total_pnl = Column(Float, default=0.0)
    largest_win = Column(Float, default=0.0)
    largest_loss = Column(Float, default=0.0)
    win_rate = Column(Float, default=0.0)
    avg_hold_time_minutes = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class OptionChainSnapshot(Base):
    __tablename__ = "option_chain_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_date = Column(Date, nullable=False, index=True)
    snapshot_time = Column(DateTime, nullable=False, index=True)
    underlying_symbol = Column(String(10), nullable=False, default="SPY")
    underlying_price = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    contracts = relationship(
        "OptionChainContract",
        back_populates="snapshot",
        cascade="all, delete-orphan",
    )


class OptionChainContract(Base):
    __tablename__ = "option_chain_contracts"
    __table_args__ = (
        Index("ix_contracts_snapshot_type", "snapshot_id", "contract_type"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_id = Column(
        Integer, ForeignKey("option_chain_snapshots.id"), nullable=False, index=True
    )
    option_symbol = Column(String(30), nullable=False)
    contract_type = Column(Enum(TradeDirection), nullable=False)
    strike_price = Column(Float, nullable=False)
    expiration_date = Column(Date, nullable=False)
    bid = Column(Float, nullable=False)
    ask = Column(Float, nullable=False)
    mid = Column(Float, nullable=False)
    delta = Column(Float, nullable=True)
    open_interest = Column(Integer, nullable=True)
    volume = Column(Integer, nullable=True)

    snapshot = relationship("OptionChainSnapshot", back_populates="contracts")
