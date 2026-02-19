from fastapi import APIRouter
from pydantic import BaseModel

from app.services.schwab_client import get_authorization_url, is_authenticated

router = APIRouter()


class AuthStatusResponse(BaseModel):
    authenticated: bool
    auth_url: str


@router.get("/auth/status", response_model=AuthStatusResponse)
def get_auth_status():
    """Check if Schwab OAuth tokens exist and return the auth URL if needed."""
    return AuthStatusResponse(
        authenticated=is_authenticated(),
        auth_url=get_authorization_url(),
    )
