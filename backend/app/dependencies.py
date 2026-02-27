from fastapi import Request

from app.services.streaming import StreamingService
from app.services.ws_manager import WebSocketManager

_ws_manager = WebSocketManager()
_streaming_service = StreamingService()


def get_ws_manager() -> WebSocketManager:
    return _ws_manager


def get_streaming_service() -> StreamingService:
    return _streaming_service


def get_schwab_service(request: Request):
    from app.services.schwab_client import SchwabService

    return SchwabService(request.app.state.schwab_client)


def get_option_selector(request: Request):
    from app.services.option_selector import OptionSelector
    from app.services.schwab_client import SchwabService

    schwab = SchwabService(request.app.state.schwab_client)
    return OptionSelector(schwab)


def get_trade_manager(request: Request):
    from app.services.option_selector import OptionSelector
    from app.services.schwab_client import SchwabService
    from app.services.trade_manager import TradeManager

    schwab = SchwabService(request.app.state.schwab_client)
    selector = OptionSelector(schwab)
    ws = get_ws_manager()
    return TradeManager(schwab, selector, ws, app=request.app)
