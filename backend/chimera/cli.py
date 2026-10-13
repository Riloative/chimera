"""Administrative command line (works while the server is stopped or running).

    python -m chimera.cli list-users
    python -m chimera.cli reset-password <login-or-email>
    python -m chimera.cli create-user <email> <login> [--admin]
    python -m chimera.cli set-role <login> admin|user
    python -m chimera.cli enable <login> | disable <login>
    python -m chimera.cli registration open|closed|first_user_only

It uses the same configuration as the server (``CHIMERA_DATA_DIR``, ``.env``).
Docker: ``docker compose exec chimera python -m chimera.cli reset-password admin``
"""

from __future__ import annotations

import argparse
import sys

from .config import REGISTRATION_MODES, ensure_secrets, load_config
from .db import Database
from .errors import AppError
from .services.auth import AuthService


def _service() -> AuthService:
    cfg = load_config()
    ensure_secrets(cfg)
    db = Database(cfg.db_path, backups_dir=cfg.backups_dir)
    return AuthService(db, cfg)


def _find(auth: AuthService, login: str) -> dict:
    user = auth.find(login)
    if not user:
        sys.exit(f"No user '{login}'.")
    return user


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m chimera.cli", description="Chimera account administration")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list-users")
    p = sub.add_parser("reset-password", help="Set a temporary password that must be changed at next login")
    p.add_argument("login")
    p = sub.add_parser("create-user")
    p.add_argument("email")
    p.add_argument("login")
    p.add_argument("--admin", action="store_true")
    p = sub.add_parser("set-role")
    p.add_argument("login")
    p.add_argument("role", choices=["admin", "user"])
    for name in ("enable", "disable"):
        sub.add_parser(name).add_argument("login")
    p = sub.add_parser("registration")
    p.add_argument("mode", choices=REGISTRATION_MODES)
    args = parser.parse_args(argv)

    auth = _service()
    try:
        if args.cmd == "list-users":
            for u in auth.db.all("SELECT * FROM users ORDER BY created_at"):
                status = "active" if u["active"] else "disabled"
                print(f"{u['username']:<24} {u['email']:<32} {u['role']:<6} {status}")
        elif args.cmd == "reset-password":
            user = _find(auth, args.login)
            temporary = auth.temporary_password()
            auth.set_password(user["id"], temporary, must_change=True)
            auth.db.execute("UPDATE users SET active = 1 WHERE id = ?", (user["id"],))
            auth.clear_throttle(f"acct:{user['username_norm']}")
            auth.clear_throttle(f"acct:{user['email_norm']}")
            print(f"Temporary password for {user['username']}: {temporary}")
            print("All sessions were signed out. A new password must be chosen at the next login.")
        elif args.cmd == "create-user":
            temporary = auth.temporary_password()
            user, _ = auth.create_user(args.email, args.login, temporary, role="admin" if args.admin else "user", must_change_password=True)
            print(f"Created {user['username']} ({user['role']}). Temporary password: {temporary}")
        elif args.cmd == "set-role":
            user = _find(auth, args.login)
            if args.role == "user" and user["role"] == "admin" and auth.admin_count() <= 1:
                sys.exit("Refusing: this is the last active administrator.")
            auth.db.execute("UPDATE users SET role = ? WHERE id = ?", (args.role, user["id"]))
            print(f"{user['username']} is now {args.role}.")
        elif args.cmd in ("enable", "disable"):
            user = _find(auth, args.login)
            if args.cmd == "disable" and user["role"] == "admin" and auth.admin_count() <= 1:
                sys.exit("Refusing: this is the last active administrator.")
            auth.db.execute("UPDATE users SET active = ? WHERE id = ?", (int(args.cmd == "enable"), user["id"]))
            if args.cmd == "disable":
                auth.revoke_all(user["id"])
            print(f"{user['username']} {args.cmd}d.")
        elif args.cmd == "registration":
            auth.set_app_setting("registration_mode", args.mode)
            print(f"Registration mode: {args.mode}")
    except AppError as exc:
        sys.exit(f"Error: {exc.message}")
    finally:
        auth.db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
