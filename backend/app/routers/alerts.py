from datetime import date, time, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import get_db
from app.models import Alert, AlertStatus
from app.schemas import AlertListResponse, AlertResponse

router = APIRouter()
settings = Settings()

ET = ZoneInfo("America/New_York")
MORNING_WINDOW = (time(9, 45), time(11, 15))
AFTERNOON_WINDOW = (time(12, 45), time(14, 50))


def _alert_in_trading_window(alert: Alert) -> bool:
    received_utc = alert.received_at.replace(tzinfo=timezone.utc)
    et_time = received_utc.astimezone(ET).time()
    windows = [MORNING_WINDOW]
    if settings.AFTERNOON_WINDOW_ENABLED:
        windows.append(AFTERNOON_WINDOW)
    return any(start <= et_time <= end for start, end in windows)


@router.get("/alerts", response_model=AlertListResponse)
def list_alerts(
    alert_date: Optional[date] = None,
    status: Optional[AlertStatus] = None,
    trading_window_only: bool = Query(False),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    query = db.query(Alert)
    if alert_date:
        query = query.filter(Alert.received_at >= str(alert_date)).filter(
            Alert.received_at < str(date.fromordinal(alert_date.toordinal() + 1))
        )
    if status:
        query = query.filter(Alert.status == status)

    if trading_window_only:
        all_alerts = query.order_by(Alert.received_at.desc()).all()
        filtered = [a for a in all_alerts if _alert_in_trading_window(a)]
        total = len(filtered)
        alerts = filtered[(page - 1) * per_page : page * per_page]
    else:
        total = query.count()
        alerts = (
            query.order_by(Alert.received_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )

    return AlertListResponse(
        alerts=[AlertResponse.model_validate(a) for a in alerts],
        total=total,
        page=page,
        per_page=per_page,
    )
