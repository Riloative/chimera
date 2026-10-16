"""Environment-based configuration.

Everything that affects security or where data lives is configured through
environment variables (see ``.env.example`` at the repository root). User-facing
preferences live in the database instead (see ``services/settings.py``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

LOOPBACK_HOSTS = ["localhost", "127.0.0.1", "::1", "[::1]"]
REGISTRATION_MODES = ("open", "closed", "first_user_only")


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _default_static_dir() -> Path | None:
    here = Path(__file__).resolve().parent
    for candidate in (here / "static", here.parent.parent / "frontend" / "dist"):
        if (candidate / "index.html").is_file():
            return candidate
    return None


@dataclass
class Config:
    host: str = "127.0.0.1"
    port: int = 8700
    data_dir: Path = field(default_factory=lambda: Path("data"))
    static_dir: Path | None = None
    # --- accounts -------------------------------------------------------
    # Default for new installs; admins can change it in the UI (stored in DB).
    registration_mode: str = "first_user_only"  # open | closed | first_user_only
    # Optional: required to create the very first (admin) account.
    setup_token: str | None = None
    # --- sessions ---------------------------------------------------------
    session_idle_hours: float = 12
    session_absolute_hours: float = 24 * 7
    session_remember_days: float = 30
    cookie_secure: str = "auto"  # auto | true | false
    # --- network ------------------------------------------------------------
    # Host header allow-list (DNS-rebinding protection). "*" disables it.
    allowed_hosts: list[str] = field(default_factory=lambda: list(LOOPBACK_HOSTS))
    # Proxies whose X-Forwarded-For / X-Forwarded-Proto headers are trusted.
    trusted_proxies: list[str] = field(default_factory=list)
    # Extra origins allowed to call the API with credentials (CORS). Empty = same-origin only.
    cors_origins: list[str] = field(default_factory=list)
    # --- rate limiting --------------------------------------------------------
    login_max_failures: int = 5
    login_window_minutes: float = 15
    login_lockout_seconds: float = 60
    register_per_hour: int = 10
    # Argon2id cost (RFC 9106 "second recommended" profile by default).
    argon2_time_cost: int = 3
    argon2_memory_kib: int = 65536
    argon2_parallelism: int = 4
    # --- secrets --------------------------------------------------------------
    # Key for encrypting provider API keys at rest (Fernet, urlsafe base64).
    encryption_key: str = ""
    # --- misc -----------------------------------------------------------------
    # Use HTTP(S)_PROXY env vars for outbound provider requests.
    http_trust_env: bool = False
    # Shared connection created for the first admin (empty string disables it).
    seed_provider_url: str = "http://127.0.0.1:8080"
    log_level: str = "info"
    # --- web reading & search (initial values; administrators change them in the UI)
    web_enabled: bool = False
    web_searxng_url: str = ""
    web_fetch_allowlist: list[str] = field(default_factory=list)
    web_user_agent: str = ""
    # Outbound proxy for web traffic only (disables DNS pinning; see docs/WEB.md).
    web_proxy: str = ""
    # Log full URLs (may contain secrets in query strings) instead of domains.
    web_verbose_log: bool = False
    # --- optional headless browser (docker compose --profile browser; docs/WEB.md)
    browser_url: str = ""
    browser_secret: str = ""
    # Where the browser's egress filter proxy listens (host:port). Only the
    # browser container should be able to reach it.
    browser_proxy_bind: str = "127.0.0.1:8701"

    @property
    def browser_configured(self) -> bool:
        return bool(self.browser_url and len(self.browser_secret) >= 16)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "chimera.sqlite"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"


def read_dotenv(path: Path) -> dict[str, str]:
    """Minimal .env parser (KEY=VALUE, comments, optional quotes)."""
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[key] = value
    return values


def load_config(env: dict[str, str] | None = None) -> Config:
    if env is None:
        # Real environment variables win over values from a .env file.
        env = {**read_dotenv(Path(os.environ.get("CHIMERA_ENV_FILE", ".env"))), **os.environ}
    env = dict(env)
    data_dir = Path(env.get("CHIMERA_DATA_DIR", "data")).expanduser().resolve()
    static = env.get("CHIMERA_STATIC_DIR")
    static_dir = Path(static).resolve() if static else _default_static_dir()

    mode = (env.get("CHIMERA_REGISTRATION_MODE") or env.get("AUTH_REGISTRATION_MODE") or "first_user_only").strip().lower()
    if mode not in REGISTRATION_MODES:
        raise ValueError(f"Invalid registration mode {mode!r}; use one of {', '.join(REGISTRATION_MODES)}")
    cookie_secure = (env.get("CHIMERA_COOKIE_SECURE") or "").strip().lower()
    if not cookie_secure:
        cookie_secure = "true" if _bool(env.get("CHIMERA_SECURE_COOKIES")) else "auto"
    if cookie_secure not in ("auto", "true", "false"):
        raise ValueError("CHIMERA_COOKIE_SECURE must be auto, true or false")

    cfg = Config(
        host=env.get("CHIMERA_HOST", "127.0.0.1"),
        port=int(env.get("CHIMERA_PORT", "8700")),
        data_dir=data_dir,
        static_dir=static_dir,
        registration_mode=mode,
        setup_token=env.get("CHIMERA_SETUP_TOKEN") or None,
        session_idle_hours=float(env.get("CHIMERA_SESSION_IDLE_HOURS", "12")),
        session_absolute_hours=float(env.get("CHIMERA_SESSION_ABSOLUTE_HOURS", str(24 * 7))),
        session_remember_days=float(env.get("CHIMERA_SESSION_REMEMBER_DAYS", "30")),
        cookie_secure=cookie_secure,
        allowed_hosts=_list(env.get("CHIMERA_ALLOWED_HOSTS")) or list(LOOPBACK_HOSTS),
        trusted_proxies=_list(env.get("CHIMERA_TRUSTED_PROXIES")),
        cors_origins=_list(env.get("CHIMERA_CORS_ORIGINS")),
        login_max_failures=int(env.get("CHIMERA_LOGIN_MAX_FAILURES", "5")),
        login_window_minutes=float(env.get("CHIMERA_LOGIN_WINDOW_MINUTES", "15")),
        login_lockout_seconds=float(env.get("CHIMERA_LOGIN_LOCKOUT_SECONDS", "60")),
        register_per_hour=int(env.get("CHIMERA_REGISTER_PER_HOUR", "10")),
        encryption_key=env.get("CHIMERA_ENCRYPTION_KEY", ""),
        argon2_time_cost=int(env.get("CHIMERA_ARGON2_TIME_COST", "3")),
        argon2_memory_kib=int(env.get("CHIMERA_ARGON2_MEMORY_KIB", "65536")),
        argon2_parallelism=int(env.get("CHIMERA_ARGON2_PARALLELISM", "4")),
        http_trust_env=_bool(env.get("CHIMERA_HTTP_TRUST_ENV")),
        seed_provider_url=env.get("CHIMERA_SEED_PROVIDER_URL", "http://127.0.0.1:8080"),
        log_level=env.get("CHIMERA_LOG_LEVEL", "info").lower(),
        web_enabled=_bool(env.get("CHIMERA_WEB_ENABLED")),
        web_searxng_url=(env.get("CHIMERA_WEB_SEARXNG_URL") or "").strip().rstrip("/"),
        web_fetch_allowlist=_list(env.get("CHIMERA_WEB_FETCH_ALLOWLIST")),
        web_user_agent=(env.get("CHIMERA_WEB_USER_AGENT") or "").strip(),
        web_proxy=(env.get("CHIMERA_WEB_PROXY") or "").strip(),
        web_verbose_log=_bool(env.get("CHIMERA_WEB_VERBOSE_LOG")),
        browser_url=(env.get("CHIMERA_BROWSER_URL") or "").strip().rstrip("/"),
        browser_secret=(env.get("CHIMERA_BROWSER_SECRET") or "").strip(),
        browser_proxy_bind=(env.get("CHIMERA_BROWSER_PROXY_BIND") or "127.0.0.1:8701").strip(),
    )
    return cfg


def _key_file(cfg: Config, name: str, factory) -> str:
    """Read a secret from the data dir, creating it (mode 0600) if missing."""
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.data_dir / name
    if path.is_file():
        value = path.read_text().strip()
        if value:
            return value
    value = factory()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(value)
    try:
        path.chmod(0o600)
    except OSError:  # pragma: no cover - e.g. Windows
        pass
    return value


def ensure_secrets(cfg: Config) -> None:
    """Load or generate the at-rest encryption key (persisted in the data dir)."""
    if not cfg.encryption_key:
        from cryptography.fernet import Fernet

        cfg.encryption_key = _key_file(cfg, ".encryption_key", lambda: Fernet.generate_key().decode())
