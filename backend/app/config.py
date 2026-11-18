import json
import os
from pathlib import Path

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 7

# Default ICE servers for WebRTC calls (STUN only — no TURN, no paid
# service). Overridable with ICE_SERVERS_JSON, a JSON array of
# RTCIceServer objects, e.g.
#   [{"urls": ["stun:stun.l.google.com:19302"]}]
DEFAULT_ICE_SERVERS = [
    {"urls": ["stun:stun.l.google.com:19302", "stun:stun1.l.google.com:19302"]}
]


def get_ice_servers() -> list:
    """Parse ICE_SERVERS_JSON; fall back to the STUN default when unset,
    invalid, or not a non-empty list of objects with a 'urls' key."""
    raw = os.environ.get("ICE_SERVERS_JSON")
    if raw:
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = None
        if (
            isinstance(parsed, list)
            and len(parsed) > 0
            and all(isinstance(item, dict) and "urls" in item for item in parsed)
        ):
            return json.loads(json.dumps(parsed))  # deep copy
    return json.loads(json.dumps(DEFAULT_ICE_SERVERS))

_BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(_BACKEND_DIR / "data")))

# Media upload cap (bytes): 1 GB for EVERY kind — photo, video, audio,
# voice. Overridable with MAX_MEDIA_BYTES (bytes) for tests and
# low-memory deployments. The two legacy names are kept so existing
# imports keep working; both equal the same 1 GB cap.
try:
    MAX_MEDIA_BYTES = int(os.environ.get("MAX_MEDIA_BYTES", 1024 * 1024 * 1024))
except (TypeError, ValueError):
    MAX_MEDIA_BYTES = 1024 * 1024 * 1024
MAX_MEDIA_BYTES = max(0, MAX_MEDIA_BYTES)
MAX_IMAGE_VIDEO_BYTES = MAX_MEDIA_BYTES
MAX_AUDIO_BYTES = MAX_MEDIA_BYTES
MAX_AVATAR_BYTES = 5 * 1024 * 1024  # 5 MB (profile pictures)
