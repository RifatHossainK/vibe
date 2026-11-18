from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import ws
from .config import DATA_DIR
from .database import Base, engine, ensure_schema
from .routers import admin, auth, config, conversations, media, users

app = FastAPI(title="Vibe")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- API routes FIRST (static serving must never shadow /api) ----
app.include_router(admin.router)
app.include_router(auth.router)
app.include_router(config.router)
app.include_router(users.router)
app.include_router(conversations.router)
app.include_router(media.router)
app.include_router(ws.router)


@app.on_event("startup")
def startup():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "uploads").mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    # Migrate pre-existing databases (adds username/avatar_path and
    # disappearing-message columns; backfills a username for every user
    # that has none).
    ensure_schema()


@app.get("/api/health")
def health():
    return {"ok": True}


# ---- Static frontend serving (single-container production deploy) ----
_FRONTEND_OUT = Path(__file__).resolve().parent.parent.parent / "frontend" / "out"


class SPAStaticFiles(StaticFiles):
    """StaticFiles that falls back to index.html for unmatched GET paths
    (SPA client routes such as /chat/123), instead of a bare 404.

    A plain StaticFiles mount answers 404 itself and never falls through to
    a later catch-all route, so the fallback has to live here.
    """

    async def get_response(self, path: str, scope):
        from starlette.exceptions import HTTPException as StarletteHTTPException

        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            response = None
        if response is None or response.status_code == 404:
            index = _FRONTEND_OUT / "index.html"
            if index.is_file():
                return FileResponse(index)
        return response


if _FRONTEND_OUT.is_dir():
    app.mount("/", SPAStaticFiles(directory=str(_FRONTEND_OUT), html=True), name="frontend")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        # /api/* must never fall through to the SPA.
        if full_path.startswith("api"):
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Not found")
        index = _FRONTEND_OUT / "index.html"
        if index.is_file():
            return FileResponse(index)
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Not found")
# If frontend/out does not exist (local dev without a frontend build),
# mounting is skipped silently and the API still works.
