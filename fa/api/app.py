"""The HTTP surface.

In local mode this is the whole server: an API plus the static files of the
progressive web app, on one port, with no authentication because it binds to
localhost. The hosted mode adds a JWT dependency here and nothing else moves —
which is the reason the client always talks to this API instead of going
straight to the database.
"""
from __future__ import annotations

import hashlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from fa.api import alerts, analysis, portfolio, tickers
from fa.api.deps import close_database, open_database

logger = logging.getLogger(__name__)

WEB_ROOT = Path(__file__).resolve().parent.parent.parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    open_database()
    logger.info("api ready")
    yield
    close_database()


def create_app(*, serve_web: bool = True) -> FastAPI:
    app = FastAPI(
        title="Financial Analyzer",
        summary="Cartera, indicadores y alertas sobre los datos ya guardados.",
        lifespan=lifespan,
    )
    # The app is served from the same origin, so this only matters when running
    # the client from a dev server on another port.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(tickers.router)
    app.include_router(portfolio.router)
    app.include_router(alerts.router)
    app.include_router(analysis.router)

    if serve_web and WEB_ROOT.is_dir():
        app.mount("/static", StaticFiles(directory=WEB_ROOT), name="static")

        @app.get("/", include_in_schema=False)
        def index() -> HTMLResponse:
            # The page names its assets by content hash, so those can be cached
            # hard — but the page itself has to be revalidated every time or the
            # browser keeps serving an old shell that points at old hashes, and
            # a shipped change never reaches anybody.
            return HTMLResponse(
                render_index(), headers={"Cache-Control": "no-cache, must-revalidate"}
            )

        @app.get("/manifest.webmanifest", include_in_schema=False)
        def manifest() -> FileResponse:
            return FileResponse(WEB_ROOT / "manifest.webmanifest")

        @app.get("/sw.js", include_in_schema=False)
        def service_worker() -> FileResponse:
            # A service worker may only control pages under its own path, so it
            # has to be served from the root rather than from /static.
            return FileResponse(WEB_ROOT / "sw.js", media_type="text/javascript")

    return app


def asset_version() -> str:
    """Short hash of the stylesheet and script, together.

    The page links its assets with this as a query string, so a browser can
    cache them hard and still pick up an edit immediately. Doing it here rather
    than writing hashes into the HTML means nobody has to remember a build step
    after changing a file.
    """
    digest = hashlib.sha256()
    for name in ("styles.css", "app.js"):
        path = WEB_ROOT / name
        if path.exists():
            digest.update(path.read_bytes())
    return digest.hexdigest()[:10]


def render_index() -> str:
    return (WEB_ROOT / "index.html").read_text(encoding="utf-8").replace(
        "{ASSETS}", asset_version()
    )


app = create_app()
