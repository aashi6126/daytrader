import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models import TradeEvent, TradeEventType

logger = logging.getLogger(__name__)


def log_trade_event(
    db: Session,
    trade_id: int,
    event_type: TradeEventType,
    message: str,
    details: Optional[Dict[str, Any]] = None,
) -> TradeEvent:
    event = TradeEvent(
        trade_id=trade_id,
        timestamp=datetime.utcnow(),
        event_type=event_type,
        message=message,
        details=json.dumps(details) if details else None,
    )
    db.add(event)
    db.flush()
    logger.debug(f"Trade #{trade_id} event: {event_type.value} - {message}")
    return event
