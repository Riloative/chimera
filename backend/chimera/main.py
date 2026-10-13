"""FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api.routes import router
from .config import Config, ensure_secret_key, load_config
from .security import SecurityMiddleware, SessionSigner
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
    ensure_secret_key(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        client = httpx.AsyncClient(trust_env=config.http_trust_env, follow_redirects=False)
        state = AppState.build(config, client)
        recovered = state.chat.recover_interrupted()
        if recovered:
            log.info("marked %d interrupted message(s) after restart", recovered)
        state.registry.seed_default(config.seed_provider_url)
        app.state.chimera = state
        log.info("Chimera %s ready (data: %s, auth: %s)", __version__, config.data_dir, "password" if config.auth_enabled else "loopback-only" if not config.allow_unauthenticated_remote else "open")
        try:
            yield
        finally:
            await state.chat.shutdown()
            await client.aclose()
            state.db.close()

    app = FastAPI(title="Chimera", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        errors = [
            {"loc": [str(p) for p in e.get("loc", [])], "msg": e.get("msg", "")} for e in exc.errors()
        ]
        first = errors[0] if errors else {"loc": [], "msg": "Invalid request"}
        field = ".".join(p for p in first["loc"] if p not in ("body", "query"))
        return JSONResponse(
            {"detail": f"{field}: {first['msg']}" if field else first["msg"], "errors": errors}, status_code=422
        )

    app.include_router(router)
    _mount_frontend(app, config.static_dir)
    app.add_middleware(SecurityMiddleware, config=config, signer=SessionSigner(config.secret_key, config.session_days))
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
