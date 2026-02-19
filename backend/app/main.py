import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import Settings
from app.dependencies import get_ws_manager
from app.models import Base
from app.routers import alerts, auth, backtest, dashboard, snapshots, stock_backtest, testing, trades, webhook
from app.routers import websocket as ws_router
from app.tasks.eod_cleanup import EODCleanupTask
from app.tasks.exit_monitor import ExitMonitorTask
from app.tasks.order_monitor import OrderMonitorTask

settings = Settings()
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    from app.database import engine

    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created")

    # Add new columns to existing tables (safe migration)
    import sqlite3
    try:
        db_path = settings.DATABASE_URL.replace("sqlite:///", "")
        conn = sqlite3.connect(db_path)
        conn.execute("ALTER TABLE trades ADD COLUMN scale_out_count INTEGER DEFAULT 0")
        conn.commit()
        conn.close()
        logger.info("Added scale_out_count column to trades table")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Initialize Schwab client (OAuth2 or Paper)
    if settings.PAPER_TRADE:
        from app.services.paper_client import PaperSchwabClient

        schwab = PaperSchwabClient()
        app.state.schwab_client = schwab
        if not settings.SCHWAB_ACCOUNT_HASH:
            accounts = schwab.linked_accounts().json()
            settings.SCHWAB_ACCOUNT_HASH = accounts[0]["hashValue"]
        logger.info("*** PAPER TRADING MODE ACTIVE ***")
    else:
        from app.services.schwab_client import get_schwab_client, is_authenticated

        if is_authenticated():
            try:
                schwab = get_schwab_client()
                app.state.schwab_client = schwab
                logger.info("Schwab client initialized (OAuth2 tokens loaded)")

                if not settings.SCHWAB_ACCOUNT_HASH:
                    accounts = schwab.linked_accounts().json()
                    if accounts:
                        settings.SCHWAB_ACCOUNT_HASH = accounts[0]["hashValue"]
                        logger.info("Account hash auto-discovered")
            except Exception as e:
                logger.warning(f"Schwab client init failed: {e}")
                app.state.schwab_client = None
        else:
            logger.warning(
                "Schwab not authenticated. Run: python -m scripts.auth_setup"
            )
            app.state.schwab_client = None

    app.state.ws_manager = get_ws_manager()
    app.state.ignore_trading_windows = False

    # Start background tasks
    tasks = []
    if app.state.schwab_client:
        tasks.append(asyncio.create_task(OrderMonitorTask(app).run()))
        tasks.append(asyncio.create_task(ExitMonitorTask(app).run()))
        tasks.append(asyncio.create_task(EODCleanupTask(app).run()))

        if settings.ACTIVE_STRATEGY == "orb_auto":
            from app.tasks.orb_signal import ORBSignalTask

            tasks.append(asyncio.create_task(ORBSignalTask(app).run()))
            logger.info("ORB auto strategy task started")

        if settings.DATA_RECORDER_ENABLED and not settings.PAPER_TRADE:
            from app.tasks.data_recorder import DataRecorderTask

            tasks.append(asyncio.create_task(DataRecorderTask(app).run()))
            logger.info("Data recorder task started")

        logger.info("Background tasks started")

    yield

    for task in tasks:
        task.cancel()
    logger.info("Background tasks cancelled")


def create_app() -> FastAPI:
    app = FastAPI(title="DayTrader 0DTE", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(alerts.router, prefix="/api", tags=["alerts"])
    app.include_router(auth.router, prefix="/api", tags=["auth"])
    app.include_router(webhook.router, prefix="/api", tags=["webhook"])
    app.include_router(trades.router, prefix="/api", tags=["trades"])
    app.include_router(dashboard.router, prefix="/api", tags=["dashboard"])
    app.include_router(testing.router, prefix="/api", tags=["testing"])
    app.include_router(snapshots.router, prefix="/api", tags=["snapshots"])
    app.include_router(backtest.router, prefix="/api", tags=["backtest"])
    app.include_router(stock_backtest.router, prefix="/api", tags=["stock-backtest"])
    app.include_router(ws_router.router, tags=["websocket"])

    # Serve frontend static files (built React app)
    frontend_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
    if frontend_dist.is_dir():
        app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="static")

        @app.get("/{full_path:path}")
        async def serve_spa(full_path: str):
            # Serve actual files if they exist, otherwise index.html for SPA routing
            file_path = frontend_dist / full_path
            if full_path and file_path.is_file():
                return FileResponse(file_path)
            return FileResponse(frontend_dist / "index.html")

    return app


app = create_app()
