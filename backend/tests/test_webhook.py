from datetime import date

from app.models import Alert, AlertStatus, Trade, TradeStatus


def _valid_payload(secret="test-secret", action="BUY_CALL", ticker="SPY"):
    return {
        "ticker": ticker,
        "action": action,
        "secret": secret,
        "price": 600.0,
        "comment": "test signal",
    }


def test_webhook_valid_call_signal(client):
    resp = client.post("/api/webhook", json=_valid_payload())
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "accepted"
    assert data["trade_id"] is not None


def test_webhook_valid_put_signal(client):
    resp = client.post("/api/webhook", json=_valid_payload(action="BUY_PUT"))
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "accepted"


def test_webhook_invalid_secret(client):
    resp = client.post("/api/webhook", json=_valid_payload(secret="wrong"))
    assert resp.status_code == 401


def test_webhook_unsupported_ticker(client):
    resp = client.post("/api/webhook", json=_valid_payload(ticker="AAPL"))
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "rejected"
    assert "SPY" in data["message"]


def test_webhook_daily_limit_reached(client, db_engine):
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=db_engine)
    db = Session()

    # Create 10 existing trades
    for i in range(10):
        trade = Trade(
            trade_date=date.today(),
            direction="CALL",
            option_symbol=f"SPY_TEST_{i}",
            strike_price=600.0,
            expiration_date=date.today(),
            entry_order_id=str(i),
            entry_quantity=1,
            status=TradeStatus.FILLED,
        )
        db.add(trade)
    db.commit()
    db.close()

    resp = client.post("/api/webhook", json=_valid_payload())
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "rejected"
    assert "limit" in data["message"].lower()


def test_webhook_invalid_action(client):
    resp = client.post(
        "/api/webhook",
        json={
            "ticker": "SPY",
            "action": "SELL_CALL",
            "secret": "test-secret",
            "price": 600.0,
        },
    )
    assert resp.status_code == 422


def test_webhook_missing_fields(client):
    resp = client.post("/api/webhook", json={"ticker": "SPY"})
    assert resp.status_code == 422


def test_webhook_creates_alert_record(client, db_engine):
    from sqlalchemy.orm import sessionmaker

    client.post("/api/webhook", json=_valid_payload())

    Session = sessionmaker(bind=db_engine)
    db = Session()
    alerts = db.query(Alert).all()
    assert len(alerts) == 1
    assert alerts[0].ticker == "SPY"
    assert alerts[0].status == AlertStatus.PROCESSED
    db.close()


def test_webhook_rejected_alert_logged(client, db_engine):
    from sqlalchemy.orm import sessionmaker

    client.post("/api/webhook", json=_valid_payload(secret="wrong"))

    Session = sessionmaker(bind=db_engine)
    db = Session()
    alerts = db.query(Alert).all()
    assert len(alerts) == 1
    assert alerts[0].status == AlertStatus.REJECTED
    assert alerts[0].rejection_reason == "Invalid secret"
    db.close()
