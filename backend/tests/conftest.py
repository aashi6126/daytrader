import os

# Set test environment before any app imports
os.environ["WEBHOOK_SECRET"] = "test-secret"
os.environ["SCHWAB_APP_KEY"] = "test-key"
os.environ["SCHWAB_APP_SECRET"] = "test-secret-value"
os.environ["SCHWAB_ACCOUNT_HASH"] = "test-hash"
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["DRY_RUN"] = "false"

from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.database as database_module
from app.database import get_db
from app.dependencies import get_ws_manager
from app.models import Base
from app.services.ws_manager import WebSocketManager
from tests.mocks.mock_schwab import MockSchwabClient


@pytest.fixture
def db_engine():
    # Use StaticPool + shared connection so all sessions see the same in-memory DB
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session(db_engine):
    Session = sessionmaker(bind=db_engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def mock_schwab():
    return MockSchwabClient()


@pytest.fixture
def ws_manager():
    return WebSocketManager()


@pytest.fixture
def app(db_engine, mock_schwab):
    # Patch the database module so lifespan and dependencies use the test engine
    original_engine = database_module.engine
    database_module.engine = db_engine

    from app.main import create_app

    application = create_app()

    TestSession = sessionmaker(bind=db_engine)

    def get_test_db():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    application.dependency_overrides[get_db] = get_test_db
    application.state.schwab_client = mock_schwab
    application.state.ws_manager = get_ws_manager()
    application.state.ignore_trading_windows = True

    yield application

    database_module.engine = original_engine


@pytest.fixture
def client(app):
    """TestClient with datetime and streaming mocked for market hours."""
    market_time = datetime(2026, 3, 2, 10, 30, tzinfo=ZoneInfo("America/New_York"))

    mock_streaming = MagicMock()
    mock_snap = MagicMock()
    mock_snap.is_stale = False
    mock_snap.last = 18.0  # Normal VIX, below circuit breaker
    mock_streaming.get_equity_quote.return_value = mock_snap

    with patch("app.services.trade_manager.datetime") as mock_dt, \
         patch("app.dependencies.get_streaming_service", return_value=mock_streaming):
        mock_dt.now.return_value = market_time
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        yield TestClient(app, raise_server_exceptions=False)
