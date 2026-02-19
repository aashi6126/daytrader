import logging
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import OptionChainContract, OptionChainSnapshot, TradeDirection

router = APIRouter()
logger = logging.getLogger(__name__)


# --- Response Schemas ---


class ContractResponse(BaseModel):
    id: int
    option_symbol: str
    contract_type: TradeDirection
    strike_price: float
    bid: float
    ask: float
    mid: float
    delta: Optional[float] = None
    open_interest: Optional[int] = None
    volume: Optional[int] = None

    model_config = {"from_attributes": True}


class SnapshotSummary(BaseModel):
    id: int
    snapshot_date: date
    snapshot_time: str
    underlying_price: float
    contract_count: int

    model_config = {"from_attributes": True}


class SnapshotListResponse(BaseModel):
    snapshots: List[SnapshotSummary]
    total: int
    page: int
    per_page: int


class SnapshotDetailResponse(BaseModel):
    snapshot: SnapshotSummary
    contracts: List[ContractResponse]


class RecordingStatsResponse(BaseModel):
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    total_snapshots: int
    total_contracts: int
    dates_covered: List[date]


# --- Endpoints ---


@router.get("/snapshots/stats/summary", response_model=RecordingStatsResponse)
def get_recording_stats(db: Session = Depends(get_db)):
    """Get statistics about recorded snapshot data."""
    stats = db.query(
        func.min(OptionChainSnapshot.snapshot_date).label("start_date"),
        func.max(OptionChainSnapshot.snapshot_date).label("end_date"),
        func.count(OptionChainSnapshot.id).label("total_snapshots"),
    ).first()

    total_contracts = db.query(func.count(OptionChainContract.id)).scalar()

    dates = (
        db.query(OptionChainSnapshot.snapshot_date.distinct())
        .order_by(OptionChainSnapshot.snapshot_date)
        .all()
    )

    return RecordingStatsResponse(
        start_date=stats.start_date,
        end_date=stats.end_date,
        total_snapshots=stats.total_snapshots or 0,
        total_contracts=total_contracts or 0,
        dates_covered=[d[0] for d in dates],
    )


@router.get("/snapshots", response_model=SnapshotListResponse)
def list_snapshots(
    snapshot_date: Optional[date] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """List recorded option chain snapshots with date filtering and pagination."""
    query = db.query(
        OptionChainSnapshot,
        func.count(OptionChainContract.id).label("contract_count"),
    ).outerjoin(
        OptionChainContract,
        OptionChainSnapshot.id == OptionChainContract.snapshot_id,
    ).group_by(OptionChainSnapshot.id)

    if snapshot_date:
        query = query.filter(OptionChainSnapshot.snapshot_date == snapshot_date)
    else:
        if start_date:
            query = query.filter(OptionChainSnapshot.snapshot_date >= start_date)
        if end_date:
            query = query.filter(OptionChainSnapshot.snapshot_date <= end_date)

    total = query.count()
    results = (
        query.order_by(OptionChainSnapshot.snapshot_time.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    snapshots = [
        SnapshotSummary(
            id=snap.id,
            snapshot_date=snap.snapshot_date,
            snapshot_time=snap.snapshot_time.isoformat(),
            underlying_price=snap.underlying_price,
            contract_count=count,
        )
        for snap, count in results
    ]

    return SnapshotListResponse(
        snapshots=snapshots,
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/snapshots/{snapshot_id}", response_model=SnapshotDetailResponse)
def get_snapshot_detail(snapshot_id: int, db: Session = Depends(get_db)):
    """Get full snapshot with all contracts."""
    snapshot = (
        db.query(OptionChainSnapshot)
        .filter(OptionChainSnapshot.id == snapshot_id)
        .first()
    )
    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    contracts = (
        db.query(OptionChainContract)
        .filter(OptionChainContract.snapshot_id == snapshot_id)
        .order_by(OptionChainContract.contract_type, OptionChainContract.strike_price)
        .all()
    )

    return SnapshotDetailResponse(
        snapshot=SnapshotSummary(
            id=snapshot.id,
            snapshot_date=snapshot.snapshot_date,
            snapshot_time=snapshot.snapshot_time.isoformat(),
            underlying_price=snapshot.underlying_price,
            contract_count=len(contracts),
        ),
        contracts=[ContractResponse.model_validate(c) for c in contracts],
    )


@router.delete("/snapshots/date/{snapshot_date}")
def delete_snapshots_by_date(snapshot_date: date, db: Session = Depends(get_db)):
    """Delete all snapshots for a specific date (cascades to contracts)."""
    deleted = (
        db.query(OptionChainSnapshot)
        .filter(OptionChainSnapshot.snapshot_date == snapshot_date)
        .delete()
    )
    db.commit()

    return {
        "status": "success",
        "message": f"Deleted {deleted} snapshots from {snapshot_date}",
    }
