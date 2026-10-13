"""``python -m chimera`` / ``chimera`` entry point."""

from __future__ import annotations

import argparse
import logging
import sys

import uvicorn

from .config import LOOPBACK_HOSTS, load_config
from .main import configure_logging, create_app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="chimera", description="Run the Chimera web app.")
    parser.add_argument("--host", help="Bind address (default from CHIMERA_HOST or 127.0.0.1)")
    parser.add_argument("--port", type=int, help="Port (default from CHIMERA_PORT or 8700)")
    args = parser.parse_args(argv)

    config = load_config()
    if args.host:
        config.host = args.host
    if args.port:
        config.port = args.port
    configure_logging(config.log_level)
    log = logging.getLogger("chimera")

    if config.host not in LOOPBACK_HOSTS and config.cookie_secure != "true":
        log.warning(
            "Listening on %s over plain HTTP: passwords and session cookies cross the network unencrypted. "
            "Use HTTPS via a reverse proxy (see docs/SECURITY.md).", config.host,
        )
    if not config.static_dir:
        log.warning("Frontend build not found; only the API will be available.")

    try:
        uvicorn.run(
            create_app(config),
            host=config.host,
            port=config.port,
            log_level=config.log_level,
            proxy_headers=False,
            server_header=False,
        )
    except KeyboardInterrupt:  # pragma: no cover
        sys.exit(0)


if __name__ == "__main__":
    main()
