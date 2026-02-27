"""AI assistant endpoint using local Ollama for trade explanations and strategy advice."""

import json
import logging
from datetime import date, timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import get_db
from app.models import Alert, Trade, TradeEvent, TradeStatus

router = APIRouter()
logger = logging.getLogger(__name__)
settings = Settings()


class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]


class ChatResponse(BaseModel):
    reply: str


SYSTEM_PROMPT = """You are a trading assistant for a 0DTE (zero days to expiration) options daytrading platform.
You analyze SPY and stock option trades, explain what happened, and provide recommendations.

Key context about this platform:
- Trades 0DTE options on SPY and individual stocks (NVDA, TSLA, AMZN, AMD, AAPL, PLTR, MSFT, GOOGL, QQQ, GLD, etc.)
- Uses automated signal strategies: ema_crossover, ema_vwap, vwap_reclaim, rsi_divergence, confluence, bb_squeeze, orb, orb_direction
- Each trade has: entry price, exit price, stop loss, trailing stop, PnL, direction (CALL/PUT), hold time
- Exit reasons: STOP_LOSS, TRAILING_STOP, PROFIT_TARGET, MAX_HOLD_TIME, TIME_BASED, MANUAL
- The system uses per-strategy exit parameters (stop_loss_percent, profit_target_percent, trailing_stop_percent, max_hold_minutes)

When analyzing trades:
- Look at entry timing, exit reason, hold duration, and PnL
- Consider bid-ask spread issues (instant stop-loss hits suggest spread problems)
- Late-day entries (after 3 PM) have extreme theta decay for 0DTE options
- Identify patterns across wins and losses

Keep responses concise and actionable. Use dollar amounts and percentages when discussing PnL.
Focus on what the trader can improve or adjust in their strategy parameters."""


def _build_context(db: Session) -> str:
    """Build trading context to inject into the conversation."""
    today = date.today()
    sections = []

    # Today's summary
    today_trades = (
        db.query(Trade)
        .filter(Trade.trade_date == today)
        .order_by(Trade.created_at.desc())
        .all()
    )
    if today_trades:
        closed = [t for t in today_trades if t.status == TradeStatus.CLOSED]
        total_pnl = sum(t.pnl_dollars or 0 for t in closed)
        wins = sum(1 for t in closed if (t.pnl_dollars or 0) > 0)
        losses = sum(1 for t in closed if (t.pnl_dollars or 0) < 0)
        active = [t for t in today_trades if t.status in (TradeStatus.FILLED, TradeStatus.STOP_LOSS_PLACED, TradeStatus.PENDING)]

        sections.append(f"TODAY ({today}):")
        sections.append(f"  Trades: {len(today_trades)} total, {wins}W/{losses}L, PnL: ${total_pnl:.2f}")
        if active:
            for t in active:
                alert = db.query(Alert).filter(Alert.trade_id == t.id).first()
                ticker = alert.ticker if alert else (t.ticker or "SPY")
                sections.append(f"  OPEN: #{t.id} {ticker} {t.direction.value} @ ${t.entry_price or 0:.2f} (status: {t.status.value})")

    # Recent trades (last 10 closed)
    recent = (
        db.query(Trade)
        .filter(Trade.status == TradeStatus.CLOSED)
        .order_by(Trade.created_at.desc())
        .limit(10)
        .all()
    )
    if recent:
        sections.append("\nRECENT CLOSED TRADES (last 10):")
        for t in recent:
            alert = db.query(Alert).filter(Alert.trade_id == t.id).first()
            ticker = alert.ticker if alert else (t.ticker or "SPY")
            hold_mins = ""
            if t.entry_filled_at and t.exit_filled_at:
                delta = (t.exit_filled_at - t.entry_filled_at).total_seconds() / 60
                hold_mins = f", held {delta:.0f}min"
            source = f", strategy={t.source}" if t.source else ""
            sections.append(
                f"  #{t.id} {t.trade_date} {ticker} {t.direction.value} "
                f"entry=${t.entry_price or 0:.2f} exit=${t.exit_price or 0:.2f} "
                f"PnL=${t.pnl_dollars or 0:.2f} ({t.pnl_percent or 0:.1f}%) "
                f"exit={t.exit_reason.value if t.exit_reason else '?'}"
                f"{hold_mins}{source}"
            )

    # Weekly PnL
    week_ago = today - timedelta(days=7)
    week_trades = (
        db.query(Trade)
        .filter(Trade.status == TradeStatus.CLOSED)
        .filter(Trade.trade_date >= week_ago)
        .all()
    )
    if week_trades:
        week_pnl = sum(t.pnl_dollars or 0 for t in week_trades)
        week_wins = sum(1 for t in week_trades if (t.pnl_dollars or 0) > 0)
        wr = week_wins / len(week_trades) * 100 if week_trades else 0
        sections.append(f"\nWEEKLY STATS: {len(week_trades)} trades, ${week_pnl:.2f} PnL, {wr:.0f}% win rate")

    # Enabled strategies
    try:
        from pathlib import Path
        strat_file = Path(__file__).resolve().parent.parent.parent / "data" / "enabled_strategies.json"
        if strat_file.exists():
            strats = json.loads(strat_file.read_text())
            if strats:
                sections.append(f"\nENABLED STRATEGIES ({len(strats)}):")
                for s in strats:
                    p = s.get("params", {})
                    sections.append(
                        f"  {s['ticker']} {s['signal_type']} @ {s['timeframe']} "
                        f"(SL={p.get('stop_loss_percent', '?')}%, PT={p.get('profit_target_percent', '?')}%, "
                        f"Trail={p.get('trailing_stop_percent', '?')}%)"
                    )
    except Exception:
        pass

    return "\n".join(sections) if sections else "No trading data available yet."


def _extract_trade_ids(messages: list[ChatMessage]) -> list[int]:
    """Extract trade IDs mentioned in the conversation."""
    import re
    ids = set()
    for msg in messages:
        # Match #123, trade 123, trade #123
        for match in re.finditer(r'(?:trade\s*)?#?(\d+)', msg.content, re.IGNORECASE):
            num = int(match.group(1))
            if 1 <= num <= 100000:
                ids.add(num)
    return sorted(ids)


def _get_trade_details(db: Session, trade_ids: list[int]) -> str:
    """Fetch detailed info for specific trades mentioned in chat."""
    if not trade_ids:
        return ""

    details = []
    for tid in trade_ids[:5]:  # max 5 trades to avoid huge context
        trade = db.query(Trade).filter(Trade.id == tid).first()
        if not trade:
            continue

        alert = db.query(Alert).filter(Alert.trade_id == tid).first()
        ticker = alert.ticker if alert else (trade.ticker or "SPY")

        events = (
            db.query(TradeEvent)
            .filter(TradeEvent.trade_id == tid)
            .order_by(TradeEvent.timestamp.asc())
            .all()
        )

        details.append(f"\nTRADE #{tid} DETAILS:")
        details.append(f"  Ticker: {ticker}, Direction: {trade.direction.value}")
        details.append(f"  Date: {trade.trade_date}, Option: {trade.option_symbol}")
        details.append(f"  Entry: ${trade.entry_price or 0:.2f} x{trade.entry_quantity}")
        details.append(f"  Exit: ${trade.exit_price or 0:.2f}, Reason: {trade.exit_reason.value if trade.exit_reason else 'N/A'}")
        details.append(f"  PnL: ${trade.pnl_dollars or 0:.2f} ({trade.pnl_percent or 0:.1f}%)")
        details.append(f"  Status: {trade.status.value}, Source: {trade.source or 'N/A'}")

        if trade.param_stop_loss_percent:
            details.append(f"  Strategy params: SL={trade.param_stop_loss_percent}%, PT={trade.param_profit_target_percent}%, Trail={trade.param_trailing_stop_percent}%")

        if events:
            details.append(f"  Events ({len(events)}):")
            for e in events:
                details.append(f"    [{e.timestamp.strftime('%H:%M:%S')}] {e.event_type.value}: {e.message}")

    return "\n".join(details)


@router.post("/assistant/chat", response_model=ChatResponse)
def chat(req: ChatRequest, db: Session = Depends(get_db)):
    # Build context
    context = _build_context(db)

    # Check for specific trade references
    trade_ids = _extract_trade_ids(req.messages)
    trade_details = _get_trade_details(db, trade_ids)

    full_system = SYSTEM_PROMPT + "\n\n--- CURRENT TRADING DATA ---\n" + context
    if trade_details:
        full_system += "\n\n--- REFERENCED TRADE DETAILS ---" + trade_details

    # Build Ollama messages (system + conversation)
    ollama_messages = [{"role": "system", "content": full_system}]
    ollama_messages.extend({"role": m.role, "content": m.content} for m in req.messages)

    try:
        resp = httpx.post(
            f"{settings.OLLAMA_URL}/api/chat",
            json={
                "model": settings.OLLAMA_MODEL,
                "messages": ollama_messages,
                "stream": False,
            },
            timeout=120.0,
        )
        resp.raise_for_status()
        data = resp.json()
        reply = data["message"]["content"]
    except httpx.ConnectError:
        raise HTTPException(
            status_code=502,
            detail=f"Cannot connect to Ollama at {settings.OLLAMA_URL}. Is Ollama running? Start it with: ollama serve",
        )
    except httpx.HTTPStatusError as e:
        logger.error(f"Ollama API error: {e}")
        if e.response.status_code == 404:
            raise HTTPException(
                status_code=502,
                detail=f"Model '{settings.OLLAMA_MODEL}' not found. Pull it with: ollama pull {settings.OLLAMA_MODEL}",
            )
        raise HTTPException(status_code=502, detail=f"Ollama error: {e.response.text}")
    except Exception as e:
        logger.error(f"Assistant error: {e}")
        raise HTTPException(status_code=500, detail="Failed to get AI response")

    return ChatResponse(reply=reply)
