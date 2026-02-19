from datetime import date

from app.models import Trade, TradeDirection, TradeStatus


def _insert_trade(db_engine, **overrides):
    from sqlalchemy.orm import sessionmaker

    defaults = dict(
        trade_date=date.today(),
        direction=TradeDirection.CALL,
        option_symbol="SPY_TEST_OPT",
        strike_price=601.0,
        expiration_date=date.today(),
        entry_order_id="ord_1",
        entry_quantity=1,
        status=TradeStatus.CLOSED,
        entry_price=2.00,
        exit_price=2.40,
        pnl_dollars=40.0,
        pnl_percent=20.0,
    )
    defaults.update(overrides)

    Session = sessionmaker(bind=db_engine)
    db = Session()
    trade = Trade(**defaults)
    db.add(trade)
    db.commit()
    trade_id = trade.id
    db.close()
    return trade_id


def test_list_trades_empty(client):
    resp = client.get("/api/trades")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["trades"] == []


def test_list_trades_with_data(client, db_engine):
    _insert_trade(db_engine)
    _insert_trade(db_engine, entry_order_id="ord_2", option_symbol="SPY_TEST_2")

    resp = client.get("/api/trades")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert len(data["trades"]) == 2


def test_list_trades_filter_by_status(client, db_engine):
    _insert_trade(db_engine, status=TradeStatus.CLOSED, entry_order_id="ord_c")
    _insert_trade(db_engine, status=TradeStatus.PENDING, entry_order_id="ord_p")

    resp = client.get("/api/trades?status=CLOSED")
    data = resp.json()
    assert data["total"] == 1
    assert data["trades"][0]["status"] == "CLOSED"


def test_list_trades_pagination(client, db_engine):
    for i in range(5):
        _insert_trade(db_engine, entry_order_id=f"ord_{i}", option_symbol=f"SPY_{i}")

    resp = client.get("/api/trades?page=1&per_page=2")
    data = resp.json()
    assert data["total"] == 5
    assert len(data["trades"]) == 2
    assert data["page"] == 1
    assert data["per_page"] == 2


def test_get_trade_by_id(client, db_engine):
    trade_id = _insert_trade(db_engine)

    resp = client.get(f"/api/trades/{trade_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == trade_id
    assert data["direction"] == "CALL"


def test_get_trade_not_found(client):
    resp = client.get("/api/trades/9999")
    assert resp.status_code == 404


def test_list_trades_filter_by_date(client, db_engine):
    _insert_trade(db_engine)

    # Query for a different date
    resp = client.get("/api/trades?trade_date=2020-01-01")
    data = resp.json()
    assert data["total"] == 0
