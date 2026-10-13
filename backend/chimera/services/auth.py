"""Accounts, password hashing, server-side sessions and brute-force throttling.

* Passwords: Argon2id (argon2-cffi). The PHC string stores the parameters, so
  hashes made with older/weaker settings are transparently upgraded on login.
* Sessions: a 256-bit random token in an HttpOnly cookie; only its SHA-256 is
  stored. Each session also carries a CSRF token (stored hashed).
* Throttling: failure counters in SQLite keyed by client IP and by account, with
  exponential lockout. They survive restarts and need no external service.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from ..config import REGISTRATION_MODES, Config
from ..db import Database, dumps, loads, new_id, now_iso
from ..errors import AppError

log = logging.getLogger("chimera.auth")

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,32}$")
RESERVED_USERNAMES = {
    "admin", "administrator", "root", "system", "api", "chimera", "support", "null", "undefined",
    "anonymous", "guest", "owner", "superuser", "sysadmin", "me", "settings", "login", "logout", "register",
}
LANGUAGES = ("en", "lt")
PASSWORD_MIN, PASSWORD_MAX = 10, 128
OWNED_TABLES = ("conversations", "prompts", "files", "collections", "providers")

_COMMON: set[str] | None = None


def common_passwords() -> set[str]:
    global _COMMON
    if _COMMON is None:
        path = Path(__file__).resolve().parent.parent / "data" / "common_passwords.txt"
        try:
            _COMMON = {ln.strip().lower() for ln in path.read_text().splitlines() if ln.strip()}
        except OSError:
            _COMMON = set()
    return _COMMON


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def normalize_email(email: str) -> str:
    return email.strip().lower()


def validate_email(email: str) -> str:
    value = email.strip()
    if len(value) > 254 or not EMAIL_RE.match(value):
        raise AppError(422, "email_invalid", "Enter a valid email address.", field="email")
    return value


def validate_username(username: str, allow_reserved: bool = False) -> str:
    value = username.strip()
    if not USERNAME_RE.match(value):
        raise AppError(
            422, "username_invalid", "Login names are 3–32 characters: letters, digits, '.', '_' or '-'.", field="username"
        )
    if not allow_reserved and value.lower() in RESERVED_USERNAMES:
        raise AppError(422, "username_reserved", "This login name is reserved.", field="username")
    return value


def validate_password(password: str, *, username: str | None = None, email: str | None = None) -> None:
    if len(password) < PASSWORD_MIN:
        raise AppError(422, "password_too_short", "Use at least 10 characters.", field="password", min=PASSWORD_MIN)
    if len(password) > PASSWORD_MAX:
        raise AppError(422, "password_too_long", "Use at most 128 characters.", field="password", max=PASSWORD_MAX)
    lowered = password.lower()
    stripped = lowered.rstrip("0123456789!?.")
    if lowered in common_passwords() or (len(stripped) >= 4 and stripped in common_passwords()) or len(set(lowered)) <= 2:
        raise AppError(422, "password_common", "This password is too common.", field="password")
    for ident in (username, (email or "").split("@")[0]):
        if ident and len(ident) >= 3 and ident.lower() in lowered:
            raise AppError(422, "password_contains_identity", "Don't include your login name or email.", field="password")


def user_out(row: dict) -> dict[str, Any]:
    return {
        "id": row["id"],
        "email": row["email"],
        "username": row["username"],
        "role": row["role"],
        "language": row["language"],
        "active": bool(row["active"]),
        "must_change_password": bool(row["must_change_password"]),
        "last_login_at": row["last_login_at"],
        "created_at": row["created_at"],
    }


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


@dataclass
class SessionInfo:
    session: dict
    user: dict


class AuthService:
    def __init__(self, db: Database, config: Config):
        self.db = db
        self.config = config
        self.hasher = PasswordHasher(
            time_cost=config.argon2_time_cost,
            memory_cost=config.argon2_memory_kib,
            parallelism=config.argon2_parallelism,
        )
        # Verified against when the account doesn't exist, so timing doesn't leak it.
        self._dummy_hash = self.hasher.hash(secrets.token_urlsafe(16))

    # -- app-level settings ---------------------------------------------------
    def app_setting(self, key: str, default: Any = None) -> Any:
        row = self.db.one("SELECT value FROM app_settings WHERE key = ?", (key,))
        return loads(row["value"], default) if row else default

    def set_app_setting(self, key: str, value: Any) -> None:
        self.db.execute(
            "INSERT INTO app_settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, dumps(value)),
        )

    def registration_mode(self) -> str:
        mode = self.app_setting("registration_mode")
        return mode if mode in REGISTRATION_MODES else self.config.registration_mode

    def user_count(self) -> int:
        return int(self.db.one("SELECT COUNT(*) AS n FROM users")["n"])  # type: ignore[index]

    def registration_open(self) -> bool:
        if self.user_count() == 0:
            return True
        return self.registration_mode() == "open"

    # -- passwords ---------------------------------------------------------------
    def hash_password(self, password: str) -> str:
        return self.hasher.hash(password)

    def verify_password(self, row: dict | None, password: str) -> bool:
        stored = row["password_hash"] if row else self._dummy_hash
        try:
            ok = self.hasher.verify(stored, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False
        if ok and row is not None and self.hasher.check_needs_rehash(stored):
            self.db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (self.hash_password(password), row["id"]))
            log.info("upgraded password hash parameters for user %s", row["id"])
        return ok and row is not None

    # -- users -------------------------------------------------------------------
    def get(self, user_id: str) -> dict | None:
        return self.db.one("SELECT * FROM users WHERE id = ?", (user_id,))

    def find(self, identifier: str) -> dict | None:
        ident = identifier.strip().lower()
        return self.db.one("SELECT * FROM users WHERE email_norm = ? OR username_norm = ?", (ident, ident))

    def _check_unique(self, email: str | None, username: str | None, exclude: str | None = None) -> None:
        if email and self.db.one(
            "SELECT id FROM users WHERE email_norm = ? AND id IS NOT ?", (normalize_email(email), exclude)
        ):
            raise AppError(409, "email_taken", "An account with this email already exists.", field="email")
        if username and self.db.one(
            "SELECT id FROM users WHERE username_norm = ? AND id IS NOT ?", (username.lower(), exclude)
        ):
            raise AppError(409, "username_taken", "This login name is already taken.", field="username")

    def create_user(
        self,
        email: str,
        username: str,
        password: str,
        *,
        role: str = "user",
        language: str | None = None,
        must_change_password: bool = False,
        enforce_first_user: bool = False,
    ) -> tuple[dict, dict]:
        """Create a user. Returns (user_row, adoption_counts)."""
        email = validate_email(email)
        is_bootstrap = enforce_first_user and self.user_count() == 0
        username = validate_username(username, allow_reserved=is_bootstrap)
        validate_password(password, username=username, email=email)
        language = language if language in LANGUAGES else None
        password_hash = self.hash_password(password)  # slow: outside the DB lock
        uid, ts = new_id(), now_iso()
        adopted: dict[str, int] = {}
        with self.db.transaction() as conn:
            first = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
            if enforce_first_user and not first and self.registration_mode() != "open":
                raise AppError(403, "registration_closed", "Registration is closed.")
            self._check_unique(email, username)
            conn.execute(
                """INSERT INTO users(id, email, email_norm, username, username_norm, password_hash, role, language,
                       must_change_password, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (uid, email, normalize_email(email), username, username.lower(), password_hash,
                 "admin" if first else role, language, int(must_change_password), ts, ts),
            )
            if first:
                adopted = self._adopt_legacy(conn, uid)
        if adopted:
            self._move_legacy_uploads(uid)
        return self.get(uid), adopted  # type: ignore[return-value]

    def _adopt_legacy(self, conn, uid: str) -> dict[str, int]:
        """The first account inherits data created before accounts existed."""
        counts: dict[str, int] = {}
        for table in OWNED_TABLES:
            n = conn.execute(f"UPDATE {table} SET user_id = ? WHERE user_id IS NULL", (uid,)).rowcount
            if n:
                counts[table] = n
        n = conn.execute("INSERT OR IGNORE INTO user_settings(user_id, key, value) SELECT ?, key, value FROM settings", (uid,)).rowcount
        conn.execute("DELETE FROM settings")
        if n:
            counts["settings"] = n
        if counts:
            conn.execute(
                "INSERT OR REPLACE INTO user_settings(user_id, key, value) VALUES (?, '_legacy_notice', ?)",
                (uid, dumps(counts)),
            )
            log.warning("first admin %s adopted pre-existing data: %s", uid, counts)
        return counts

    def _move_legacy_uploads(self, uid: str) -> None:
        uploads = self.config.uploads_dir
        rows = self.db.all("SELECT id, storage_name FROM files WHERE user_id = ? AND storage_name IS NOT NULL", (uid,))
        for row in rows:
            name = row["storage_name"]
            if "/" in name or not re.fullmatch(r"[0-9a-f]{32}", name):
                continue
            src = uploads / name
            dest_dir = uploads / uid
            dest_dir.mkdir(parents=True, exist_ok=True)
            if src.is_file():
                shutil.move(str(src), dest_dir / name)
            self.db.execute("UPDATE files SET storage_name = ? WHERE id = ?", (f"{uid}/{name}", row["id"]))

    def update_profile(self, user_id: str, *, email: str | None = None, username: str | None = None, language: str | None = None) -> dict:
        sets, values = [], []
        if email is not None:
            email = validate_email(email)
            self._check_unique(email, None, exclude=user_id)
            sets += ["email = ?", "email_norm = ?"]
            values += [email, normalize_email(email)]
        if username is not None:
            username = validate_username(username)
            self._check_unique(None, username, exclude=user_id)
            sets += ["username = ?", "username_norm = ?"]
            values += [username, username.lower()]
        if language is not None:
            if language not in LANGUAGES:
                raise AppError(422, "language_invalid", "Unsupported language.", field="language")
            sets.append("language = ?")
            values.append(language)
        if sets:
            sets.append("updated_at = ?")
            values.append(now_iso())
            self.db.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", (*values, user_id))
        return self.get(user_id)  # type: ignore[return-value]

    def set_password(self, user_id: str, password: str, *, must_change: bool = False, keep_session: str | None = None) -> None:
        user = self.get(user_id)
        if not user:
            raise AppError(404, "user_not_found", "User not found.")
        validate_password(password, username=user["username"], email=user["email"])
        self.db.execute(
            "UPDATE users SET password_hash = ?, must_change_password = ?, updated_at = ? WHERE id = ?",
            (self.hash_password(password), int(must_change), now_iso(), user_id),
        )
        self.revoke_all(user_id, except_session=keep_session)

    @staticmethod
    def temporary_password() -> str:
        alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        return "-".join("".join(secrets.choice(alphabet) for _ in range(5)) for _ in range(4))

    def admin_count(self, active_only: bool = True) -> int:
        sql = "SELECT COUNT(*) AS n FROM users WHERE role = 'admin'" + (" AND active = 1" if active_only else "")
        return int(self.db.one(sql)["n"])  # type: ignore[index]

    # -- sessions ------------------------------------------------------------------
    def _lifetimes(self, remember: bool) -> tuple[timedelta, timedelta]:
        if remember:
            days = timedelta(days=self.config.session_remember_days)
            return days, days
        return timedelta(hours=self.config.session_idle_hours), timedelta(hours=self.config.session_absolute_hours)

    def create_session(self, user_id: str, *, remember: bool, user_agent: str | None, ip: str | None) -> tuple[str, str]:
        """Returns (session_token, csrf_token). Only hashes are stored."""
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        _, absolute = self._lifetimes(remember)
        now = datetime.now(timezone.utc)
        self.db.execute("DELETE FROM sessions WHERE expires_at < ?", (_iso(now),))
        self.db.execute(
            """INSERT INTO sessions(id, user_id, token_hash, csrf_hash, remember, user_agent, ip, created_at, last_used_at, expires_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (new_id(), user_id, sha256(token), sha256(csrf), int(remember), (user_agent or "")[:300], ip, _iso(now), _iso(now), _iso(now + absolute)),
        )
        self.db.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (_iso(now), user_id))
        return token, csrf

    def resolve(self, token: str | None) -> SessionInfo | None:
        if not token or len(token) > 200:
            return None
        session = self.db.one("SELECT * FROM sessions WHERE token_hash = ?", (sha256(token),))
        if not session:
            return None
        now = datetime.now(timezone.utc)
        idle, _ = self._lifetimes(bool(session["remember"]))
        last_used = _parse(session["last_used_at"])
        if _parse(session["expires_at"]) <= now or last_used + idle <= now:
            self.db.execute("DELETE FROM sessions WHERE id = ?", (session["id"],))
            return None
        user = self.get(session["user_id"])
        if not user or not user["active"]:
            return None
        if (now - last_used).total_seconds() > 60:
            self.db.execute("UPDATE sessions SET last_used_at = ? WHERE id = ?", (_iso(now), session["id"]))
        return SessionInfo(session=session, user=user)

    @staticmethod
    def csrf_valid(session: dict | None, header_token: str | None, cookie_token: str | None) -> bool:
        if not header_token or not cookie_token or not hmac.compare_digest(header_token, cookie_token):
            return False
        if session is None:
            return True  # pre-login double-submit (login/register forms)
        return hmac.compare_digest(sha256(header_token), session["csrf_hash"])

    def sessions_for(self, user_id: str) -> list[dict]:
        now = _iso(datetime.now(timezone.utc))
        return self.db.all(
            "SELECT id, created_at, last_used_at, expires_at, user_agent, ip, remember FROM sessions WHERE user_id = ? AND expires_at > ? ORDER BY last_used_at DESC",
            (user_id, now),
        )

    def revoke(self, user_id: str, session_id: str) -> bool:
        return self.db.execute("DELETE FROM sessions WHERE id = ? AND user_id = ?", (session_id, user_id)).rowcount > 0

    def revoke_all(self, user_id: str, except_session: str | None = None) -> int:
        return self.db.execute(
            "DELETE FROM sessions WHERE user_id = ? AND id IS NOT ?", (user_id, except_session)
        ).rowcount

    # -- throttling ------------------------------------------------------------------
    def _locked_for(self, key: str) -> float:
        row = self.db.one("SELECT locked_until FROM auth_throttle WHERE key = ?", (key,))
        return max(0.0, row["locked_until"] - time.time()) if row else 0.0

    def check_throttle(self, keys: list[str]) -> None:
        wait = max((self._locked_for(k) for k in keys), default=0.0)
        if wait > 0:
            raise AppError(429, "rate_limited", "Too many attempts. Try again later.", retry_after=int(wait) + 1)

    def record_failure(self, key: str, threshold: int) -> None:
        now = time.time()
        window = self.config.login_window_minutes * 60
        row = self.db.one("SELECT * FROM auth_throttle WHERE key = ?", (key,))
        if not row or now - row["window_start"] > window and row["locked_until"] < now:
            failures, start = 1, now
        else:
            failures, start = row["failures"] + 1, row["window_start"]
        locked = 0.0
        if failures >= threshold:
            # Exponential back-off: base, 2x, 4x … capped at one hour.
            locked = now + min(3600.0, self.config.login_lockout_seconds * 2 ** (failures - threshold))
        self.db.execute(
            "INSERT OR REPLACE INTO auth_throttle(key, failures, window_start, locked_until) VALUES (?,?,?,?)",
            (key, failures, start, locked),
        )

    def clear_throttle(self, key: str) -> None:
        self.db.execute("DELETE FROM auth_throttle WHERE key = ?", (key,))

    def check_register_rate(self, ip: str) -> None:
        key, now = f"reg:{ip}", time.time()
        row = self.db.one("SELECT * FROM auth_throttle WHERE key = ?", (key,))
        if row and now - row["window_start"] < 3600:
            if row["failures"] >= self.config.register_per_hour:
                raise AppError(429, "rate_limited", "Too many attempts. Try again later.", retry_after=int(3600 - (now - row["window_start"])) + 1)
            self.db.execute("UPDATE auth_throttle SET failures = failures + 1 WHERE key = ?", (key,))
        else:
            self.db.execute("INSERT OR REPLACE INTO auth_throttle(key, failures, window_start, locked_until) VALUES (?,1,?,0)", (key, now))

    def login(self, identifier: str, password: str, ip: str) -> dict:
        """Verify credentials with throttling. Raises a generic error on failure."""
        ident = identifier.strip().lower()[:254]
        ip_key, acct_key = f"ip:{ip}", f"acct:{ident}"
        self.check_throttle([ip_key, acct_key])
        user = self.find(ident) if ident else None
        ok = self.verify_password(user, password)  # constant work for unknown users
        if not ok or not user:
            self.record_failure(acct_key, self.config.login_max_failures)
            self.record_failure(ip_key, self.config.login_max_failures * 4)
            raise AppError(401, "invalid_credentials", "Incorrect login or password.")
        if not user["active"]:
            # Only revealed after a correct password.
            raise AppError(403, "account_disabled", "This account is disabled.")
        self.clear_throttle(acct_key)
        self.clear_throttle(ip_key)
        return user
