from datetime import date, datetime

from app.models import Trade, TradeDirection, TradeStatus


def _insert_closed_trade(db_engine, pnl_dollars=40.0, entry_order_id="ord_1"):
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=db_engine)
    db = Session()
    trade = Trade(
        trade_date=date.today(),
        direction=TradeDirection.CALL,
        option_symbol="SPY_TEST_OPT",
        strike_price=601.0,
        expiration_date=date.today(),
        entry_order_id=entry_order_id,
        entry_price=2.00,
        entry_quantity=1,
        entry_filled_at=datetime(2026, 2, 7, 14, 0),
        exit_price=2.00 + pnl_dollars / 100,
        exit_filled_at=datetime(2026, 2, 7, 14, 30),
        pnl_dollars=pnl_dollars,
        pnl_percent=(pnl_dollars / 200) * 100,
        status=TradeStatus.CLOSED,
    )
    db.add(trade)
    db.commit()
    db.close()


def test_daily_stats_no_trades(client):
    resp = client.get("/api/dashboard/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_trades"] == 0
    assert data["total_pnl"] == 0.0
    assert data["trades_remaining"] == 10


def test_daily_stats_with_trades(client, db_engine):
    _insert_closed_trade(db_engine, pnl_dollars=40.0, entry_order_id="ord_1")
    _insert_closed_trade(db_engine, pnl_dollars=-20.0, entry_order_id="ord_2")

    resp = client.get("/api/dashboard/stats")
    data = resp.json()
    assert data["total_trades"] == 2
    assert data["winning_trades"] == 1
    assert data["losing_trades"] == 1
    assert data["total_pnl"] == 20.0
    assert data["win_rate"] == 50.0


def test_daily_stats_trades_remaining(client, db_engine):
    for i in range(3):
        _insert_closed_trade(db_engine, pnl_dollars=10.0, entry_order_id=f"ord_{i}")

    resp = client.get("/api/dashboard/stats")
    data = resp.json()
    assert data["trades_remaining"] == 7


def test_pnl_chart_empty(client):
    resp = client.get("/api/dashboard/pnl")
    assert resp.status_code == 200
    data = resp.json()
    assert data["data_points"] == []
    assert data["total_pnl"] == 0.0


def test_pnl_chart_with_closed_trades(client, db_engine):
    _insert_closed_trade(db_engine, pnl_dollars=40.0, entry_order_id="ord_1")
    _insert_closed_trade(db_engine, pnl_dollars=-10.0, entry_order_id="ord_2")

    resp = client.get("/api/dashboard/pnl")
    data = resp.json()
    assert len(data["data_points"]) == 2
    assert data["total_pnl"] == 30.0
    # Cumulative: first=40, second=30
    assert data["data_points"][0]["cumulative_pnl"] == 40.0
    assert data["data_points"][1]["cumulative_pnl"] == 30.0


def test_pnl_chart_date_filter(client, db_engine):
    _insert_closed_trade(db_engine, pnl_dollars=40.0)

    resp = client.get("/api/dashboard/pnl?trade_date=2020-01-01")
    data = resp.json()
    assert data["data_points"] == []
    assert data["total_pnl"] == 0.0
