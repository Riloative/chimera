"""Environment-based configuration.

Everything that affects security or where data lives is configured through
environment variables (see ``.env.example`` at the repository root). User-facing
preferences live in the database instead (see ``services/settings.py``).
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

LOOPBACK_HOSTS = ["localhost", "127.0.0.1", "::1", "[::1]"]


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
    # Authentication. When a password is set, every API call needs a session.
    password: str | None = None
    secret_key: str = ""
    session_days: int = 30
    # Without a password, only loopback clients are served unless this is set
    # (e.g. Docker with the port published on 127.0.0.1 only).
    allow_unauthenticated_remote: bool = False
    # Host header allow-list (DNS-rebinding protection). "*" disables it.
    allowed_hosts: list[str] = field(default_factory=list)
    # Use HTTP(S)_PROXY env vars for outbound provider requests.
    http_trust_env: bool = False
    # Provider created on first start (empty string disables seeding).
    seed_provider_url: str = "http://127.0.0.1:8080"
    log_level: str = "info"
    secure_cookies: bool = False

    @property
    def db_path(self) -> Path:
        return self.data_dir / "chimera.sqlite"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def auth_enabled(self) -> bool:
        return bool(self.password)


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

    password = env.get("CHIMERA_PASSWORD") or None
    allowed_hosts = _list(env.get("CHIMERA_ALLOWED_HOSTS"))
    if not allowed_hosts:
        # With a password, the session cookie already defeats DNS rebinding,
        # so any Host is acceptable. Without one, stay on loopback names.
        allowed_hosts = ["*"] if password else list(LOOPBACK_HOSTS)

    cfg = Config(
        host=env.get("CHIMERA_HOST", "127.0.0.1"),
        port=int(env.get("CHIMERA_PORT", "8700")),
        data_dir=data_dir,
        static_dir=static_dir,
        password=password,
        secret_key=env.get("CHIMERA_SECRET_KEY", ""),
        session_days=int(env.get("CHIMERA_SESSION_DAYS", "30")),
        allow_unauthenticated_remote=_bool(env.get("CHIMERA_ALLOW_UNAUTHENTICATED_REMOTE")),
        allowed_hosts=allowed_hosts,
        http_trust_env=_bool(env.get("CHIMERA_HTTP_TRUST_ENV")),
        seed_provider_url=env.get("CHIMERA_SEED_PROVIDER_URL", "http://127.0.0.1:8080"),
        log_level=env.get("CHIMERA_LOG_LEVEL", "info").lower(),
        secure_cookies=_bool(env.get("CHIMERA_SECURE_COOKIES")),
    )
    return cfg


def ensure_secret_key(cfg: Config) -> None:
    """Load or create a persistent secret used to sign session cookies."""
    if cfg.secret_key:
        return
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    key_file = cfg.data_dir / ".secret_key"
    if key_file.is_file():
        cfg.secret_key = key_file.read_text().strip()
    if not cfg.secret_key:
        cfg.secret_key = secrets.token_urlsafe(48)
        key_file.write_text(cfg.secret_key)
        try:
            key_file.chmod(0o600)
        except OSError:  # pragma: no cover - e.g. Windows
            pass
