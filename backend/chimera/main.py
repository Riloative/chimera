"""FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api.auth_routes import router as auth_router
from .api.routes import router
from .api.web_routes import router as web_router
from .config import Config, ensure_secrets, load_config
from .errors import AppError
from .security import SecurityMiddleware
from .state import AppState

log = logging.getLogger("chimera")


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # Request lines can contain conversation IDs but never content; keep them.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def create_app(config: Config | None = None) -> FastAPI:
    config = config or load_config()
    ensure_secrets(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        client = httpx.AsyncClient(trust_env=config.http_trust_env, follow_redirects=False)
        state = AppState.build(config, client)
        web_client = state.web.fetcher.client
        browser = state.web.browser
        if browser is not None:
            host, _, port = config.browser_proxy_bind.rpartition(":")
            try:
                await browser.proxy.start(host.strip("[]") or "127.0.0.1", int(port))
            except (OSError, ValueError) as exc:
                log.error("browser egress proxy could not start on %s (%s); the headless browser is disabled",
                          config.browser_proxy_bind, exc)
        elif config.browser_url:
            log.warning("CHIMERA_BROWSER_URL is set but CHIMERA_BROWSER_SECRET is missing or shorter than 16 "
                        "characters; the headless browser is disabled")
        recovered = state.chat.recover_interrupted()
        if recovered:
            log.info("marked %d interrupted message(s) after restart", recovered)
        encrypted = state.registry.encrypt_legacy_keys()
        if encrypted:
            log.info("encrypted %d stored API key(s) at rest", encrypted)
        if state.db.last_backup:
            log.warning("pre-upgrade backup: %s (restore by copying it over %s while stopped)", state.db.last_backup, config.db_path)
        app.state.chimera = state
        users = state.auth.user_count()
        log.info(
            "Chimera %s ready (data: %s, users: %d, registration: %s)",
            __version__, config.data_dir, users, state.auth.registration_mode(),
        )
        if users == 0:
            log.warning("No accounts yet: open the web UI and register; the first account becomes the administrator.")
        try:
            yield
        finally:
            await state.chat.shutdown()
            await client.aclose()
            await web_client.aclose()
            if browser is not None:
                await browser.proxy.stop()
                await browser.aclose()
            state.db.close()

    app = FastAPI(title="Chimera", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        headers = {"Retry-After": str(exc.params["retry_after"])} if "retry_after" in exc.params else None
        return exc.response(headers)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        errors = exc.errors()
        first = errors[0] if errors else {}
        field = ".".join(str(p) for p in first.get("loc", []) if p not in ("body", "query", "path"))
        return AppError(
            422, "validation_error", f"{field}: {first.get('msg', 'Invalid request')}" if field else "Invalid request",
            field=field or None, type=first.get("type"),
        ).response()

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException):
        return AppError(exc.status_code, f"http_{exc.status_code}", str(exc.detail)).response()

    app.include_router(auth_router)
    app.include_router(web_router)
    app.include_router(router)  # last: ends with the /api catch-all
    _mount_frontend(app, config.static_dir)
    app.add_middleware(SecurityMiddleware, config=config)
    if config.cors_origins:
        # Same-origin by default; only explicitly configured origins get CORS.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=config.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Content-Type", "X-CSRF-Token"],
        )
    return app


def _mount_frontend(app: FastAPI, static_dir: Path | None) -> None:
    if not static_dir or not (static_dir / "index.html").is_file():
        @app.get("/", include_in_schema=False)
        async def no_frontend():
            return PlainTextResponse(
                "Chimera API is running, but the web interface has not been built.\n"
                "Run `npm ci && npm run build` in ./frontend (or use Docker).",
                status_code=503,
            )
        return

    index = static_dir / "index.html"
    if (static_dir / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

    root = static_dir.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        candidate = (root / path).resolve()
        if path and candidate.is_file() and root in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
