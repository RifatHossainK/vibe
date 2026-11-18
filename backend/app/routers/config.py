"""Client configuration endpoint (ICE servers for WebRTC calls)."""

from fastapi import APIRouter, Depends

from .. import models
from ..config import get_ice_servers
from ..security import get_current_user

router = APIRouter(tags=["config"])


@router.get("/api/config")
def get_config(current_user: models.User = Depends(get_current_user)):
    return {"ice_servers": get_ice_servers()}
