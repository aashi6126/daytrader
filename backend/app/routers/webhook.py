import json
import logging
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import get_db
from app.dependencies import get_trade_manager
from app.models import Alert, AlertStatus
from app.schemas import TradingViewAlert, WebhookResponse
from app.services.trade_manager import TradeManager

router = APIRouter()
logger = logging.getLogger(__name__)
settings = Settings()

ALERT_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
ALERT_LOG_DIR.mkdir(exist_ok=True)
ALERT_LOG_FILE = ALERT_LOG_DIR / "alerts.log"

ET = ZoneInfo("America/New_York")
MORNING_WINDOW = (time(9, 45), time(11, 15))
AFTERNOON_WINDOW = (time(12, 45), time(14, 50))


def _get_trading_windows():
    windows = [MORNING_WINDOW]
    if settings.AFTERNOON_WINDOW_ENABLED:
        windows.append(AFTERNOON_WINDOW)
    return windows


def _in_trading_window() -> bool:
    now_et = datetime.now(ET).time()
    return any(start <= now_et <= end for start, end in _get_trading_windows())


@router.post("/webhook", response_model=WebhookResponse)
async def receive_webhook(
    request: Request,
    db: Session = Depends(get_db),
    trade_manager: TradeManager = Depends(get_trade_manager),
):
    # TradingView sends Content-Type: text/plain, so parse raw body manually
    raw_body = await request.body()
    raw_text = raw_body.decode("utf-8")
    content_type = request.headers.get("content-type", "unknown")
    logger.info(f"Webhook received (content-type: {content_type}): {raw_text}")

    # Log every incoming request to file, even if parsing fails
    with open(ALERT_LOG_FILE, "a") as f:
        f.write(json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "received",
            "content_type": content_type,
            "raw_body": raw_text,
        }) + "\n")

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        logger.error(f"Invalid JSON from webhook: {raw_text!r}")
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    try:
        alert = TradingViewAlert(**payload)
    except ValidationError as e:
        logger.error(f"Validation error: {e.errors()}")
        raise HTTPException(status_code=422, detail=e.errors())

    # Determine source
    source = alert.source if alert.source else "tradingview"

    # Log alert to file
    log_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "ticker": alert.ticker,
        "action": alert.action,
        "price": alert.price,
        "comment": alert.comment,
        "raw_body": raw_text,
    }
    with open(ALERT_LOG_FILE, "a") as f:
        f.write(json.dumps(log_entry) + "\n")

    db_alert = Alert(
        raw_payload=raw_text,
        ticker=alert.ticker,
        direction=alert.direction,
        signal_price=alert.price,
        source=source,
        status=AlertStatus.RECEIVED,
    )
    db.add(db_alert)
    db.flush()

    # Authenticate
    if alert.secret != settings.WEBHOOK_SECRET:
        db_alert.status = AlertStatus.REJECTED
        db_alert.rejection_reason = "Invalid secret"
        db.commit()
        logger.warning("Webhook rejected: invalid secret")
        raise HTTPException(status_code=401, detail="Invalid webhook secret")

    # Validate ticker
    if alert.ticker.upper() != "SPY":
        db_alert.status = AlertStatus.REJECTED
        db_alert.rejection_reason = f"Unsupported ticker: {alert.ticker}"
        db.commit()
        return WebhookResponse(status="rejected", message="Only SPY is supported")

    # Dedup: reject if identical alert (same action + direction) arrived recently
    dedup_cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.DEDUP_WINDOW_SECONDS)
    dup = (
        db.query(Alert)
        .filter(Alert.id != db_alert.id)
        .filter(Alert.received_at >= dedup_cutoff)
        .filter(Alert.direction == alert.direction)
        .filter(Alert.status != AlertStatus.REJECTED)
        .first()
    )
    if dup and alert.action != "CLOSE":
        db_alert.status = AlertStatus.REJECTED
        db_alert.rejection_reason = f"Duplicate alert (within {settings.DEDUP_WINDOW_SECONDS}s)"
        db.commit()
        logger.info(f"Webhook rejected: duplicate of alert #{dup.id}")
        return WebhookResponse(status="rejected", message="Duplicate alert ignored")

    # CLOSE signals are always allowed (risk-reducing)
    # BUY signals require an active trading window
    try:
        if alert.action == "CLOSE":
            result = await trade_manager.close_open_position(db, db_alert)
        else:
            ignore_windows = getattr(request.app.state, "ignore_trading_windows", False)
            if not _in_trading_window() and not ignore_windows:
                now_et = datetime.now(ET).strftime("%H:%M")
                db_alert.status = AlertStatus.REJECTED
                db_alert.rejection_reason = f"Outside trading window ({now_et} ET)"
                db.commit()
                logger.info(f"Webhook rejected: outside trading window ({now_et} ET)")
                return WebhookResponse(
                    status="rejected",
                    message=f"Outside trading window ({now_et} ET). "
                            f"Windows: 09:35-11:15, 12:45-14:50",
                )
            result = await trade_manager.process_alert(db, db_alert, alert)
        return result
    except Exception as e:
        db_alert.status = AlertStatus.ERROR
        db_alert.rejection_reason = str(e)
        db.commit()
        logger.exception("Error processing webhook alert")
        raise HTTPException(
            status_code=500, detail=f"Error processing alert: {str(e)}"
        )
