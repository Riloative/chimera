"""``python -m chimera`` / ``chimera`` entry point.

``chimera`` runs the server; ``chimera <command>`` runs an administrative
command (``backup``, ``restore``, ``reset-password``…, see ``chimera.cli``).
"""

from __future__ import annotations

import argparse
import logging
import sys

from . import BUILD_COMMIT, __version__
from .config import LOOPBACK_HOSTS, load_config, validate_config
from .db import SCHEMA_VERSION, schema_version_of
from .logs import configure_logging


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    from .cli import COMMANDS

    if argv and argv[0] in COMMANDS:
        from .cli import main as cli_main

        sys.exit(cli_main(argv))

    parser = argparse.ArgumentParser(prog="chimera", description="Run the Chimera web app. "
                                     f"Administrative commands: {', '.join(COMMANDS)} (chimera <command> --help).")
    parser.add_argument("--host", help="Bind address (default from CHIMERA_HOST or 127.0.0.1)")
    parser.add_argument("--port", type=int, help="Port (default from CHIMERA_PORT or 8700)")
    parser.add_argument("--check-config", action="store_true", help="Validate the configuration and exit")
    parser.add_argument("--version", action="version", version=f"Chimera {__version__}")
    args = parser.parse_args(argv)

    try:
        config = load_config()
    except ValueError as exc:
        sys.exit(f"Configuration error: {exc}")
    if args.host:
        config.host = args.host
    if args.port:
        config.port = args.port
    errors, warnings = validate_config(config)
    log_config = configure_logging(config.log_level if not errors else "info",
                                   config.log_format if config.log_format in ("text", "json") else "text")
    log = logging.getLogger("chimera")
    for message in warnings:
        log.warning("Configuration: %s", message)
    for message in errors:
        log.error("Configuration error: %s", message)
    if errors:
        log.error("Not starting: fix the configuration above (.env or environment) and start again.")
        sys.exit(2)
    if args.check_config:
        print("Configuration OK" + (f" ({len(warnings)} warning(s))" if warnings else ""))
        return

    # Downgrade guard, before anything opens the database.
    found = schema_version_of(config.db_path) if config.db_path.exists() else None
    if found is not None and found > SCHEMA_VERSION:
        from .db import SchemaTooNew

        log.error("%s", SchemaTooNew(found, SCHEMA_VERSION, config.db_path, config.backups_dir))
        sys.exit(3)

    if config.host not in LOOPBACK_HOSTS and config.cookie_secure != "true" and not config.trusted_proxies:
        log.warning(
            "Listening on %s over plain HTTP: passwords and session cookies cross the network unencrypted. "
            "Use HTTPS via a reverse proxy (see docs/SECURITY.md).", config.host,
        )
    if not config.static_dir:
        log.warning("Frontend build not found; only the API will be available.")
    log.info("Chimera %s%s starting", __version__, f" ({BUILD_COMMIT})" if BUILD_COMMIT else "")

    import uvicorn

    from .main import create_app

    try:
        uvicorn.run(
            create_app(config),
            host=config.host,
            port=config.port,
            log_level=config.log_level,
            log_config=log_config,
            proxy_headers=False,
            server_header=False,
            # Open streams get this long to finish on shutdown; then running
            # answers are saved as interrupted (ChatEngine.shutdown).
            timeout_graceful_shutdown=config.shutdown_grace_seconds,
        )
    except KeyboardInterrupt:  # pragma: no cover
        sys.exit(0)


if __name__ == "__main__":
    main()
