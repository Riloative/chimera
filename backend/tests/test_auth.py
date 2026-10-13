import time
from datetime import datetime, timedelta, timezone

import pytest

from chimera.main import create_app

from .conftest import PASSWORD, CsrfClient, login, make_config, new_client, register


def code(resp):
    return resp.json()["detail"]["code"]


# ------------------------------------------------------------- registration
def test_first_user_becomes_admin_and_registration_closes(anon):
    status = anon.get("/api/auth/status").json()
    assert status == {**status, "authenticated": False, "needs_setup": True, "registration_open": True}
    resp = register(anon, "alice", language="lt")
    assert resp.status_code == 201
    me = resp.json()
    assert me["role"] == "admin" and me["username"] == "alice" and me["language"] == "lt"
    assert "password_hash" not in me
    assert anon.get("/api/auth/status").json()["registration_open"] is False  # first_user_only
    other = new_client(anon.app)
    assert code(register(other, "bob")) == "registration_closed"


def test_open_mode_creates_regular_users(app, client):
    assert client.patch("/api/admin/settings", json={"registration_mode": "open"}).json()["registration_mode"] == "open"
    other = new_client(app)
    resp = register(other, "bob")
    assert resp.status_code == 201 and resp.json()["role"] == "user"
    assert client.patch("/api/admin/settings", json={"registration_mode": "closed"}).status_code == 200
    third = new_client(app)
    assert code(register(third, "carol")) == "registration_closed"


def test_env_registration_mode_default(tmp_path):
    app = create_app(make_config(tmp_path, registration_mode="closed"))
    with CsrfClient(app, base_url="http://localhost") as c:
        c.get("/api/auth/status")
        assert register(c, "alice").status_code == 201  # the very first account is always allowed
        other = new_client(app)
        assert code(register(other, "bob")) == "registration_closed"


@pytest.mark.parametrize(
    "fields, expected",
    [
        ({"email": "not-an-email"}, "email_invalid"),
        ({"username": "ab"}, "username_invalid"),
        ({"username": "bad name!"}, "username_invalid"),
        ({"password": "short", "password_confirm": "short"}, "password_too_short"),
        ({"password": "password1234", "password_confirm": "password1234"}, "password_common"),
        ({"password": "aaaaaaaaaaaa", "password_confirm": "aaaaaaaaaaaa"}, "password_common"),
        ({"password_confirm": "something else entirely"}, "password_mismatch"),
    ],
)
def test_registration_validation(anon, fields, expected):
    body = {"email": "alice@example.com", "username": "alice", "password": PASSWORD, "password_confirm": PASSWORD, **fields}
    resp = anon.post("/api/auth/register", json=body)
    assert resp.status_code in (403, 422) and code(resp) == expected


def test_duplicates_are_case_insensitive(app, client):
    client.patch("/api/admin/settings", json={"registration_mode": "open"})
    other = new_client(app)
    assert code(register(other, "ALICE", email="new@example.com")) == "username_taken"
    assert code(register(other, "newname", email="Alice@Example.COM")) == "email_taken"


def test_reserved_names_allowed_only_for_bootstrap(app, anon):
    assert register(anon, "admin").status_code == 201  # first account may be called admin
    anon.patch("/api/admin/settings", json={"registration_mode": "open"})
    other = new_client(app)
    assert code(register(other, "root")) == "username_reserved"


def test_setup_token_required_when_configured(tmp_path):
    app = create_app(make_config(tmp_path, setup_token="let-me-in"))
    with CsrfClient(app, base_url="http://localhost") as c:
        assert c.get("/api/auth/status").json()["setup_token_required"] is True
        assert code(register(c, "alice")) == "setup_token_invalid"
        assert register(c, "alice", setup_token="let-me-in").status_code == 201


# -------------------------------------------------------------------- login
def test_login_with_email_or_username(app, client):
    other = new_client(app)
    assert login(other, "alice").status_code == 200
    third = new_client(app)
    assert login(third, "ALICE@example.com").json()["username"] == "alice"


def test_login_failures_are_generic(app, client):
    c = new_client(app)
    wrong = login(c, "alice", "wrong password!!")
    unknown = login(c, "nobody", "wrong password!!")
    assert wrong.status_code == unknown.status_code == 401
    assert code(wrong) == code(unknown) == "invalid_credentials"
    assert wrong.json()["detail"]["message"] == unknown.json()["detail"]["message"]


def test_unknown_user_does_comparable_hashing_work(tmp_path):
    # Real-cost hashing so the timing comparison is meaningful.
    app = create_app(make_config(tmp_path, argon2_time_cost=2, argon2_memory_kib=16384))
    with CsrfClient(app, base_url="http://localhost") as c:
        c.get("/api/auth/status")
        register(c, "alice")
        c.post("/api/auth/logout")

        def timed(identifier):
            start = time.perf_counter()
            login(c, identifier, "definitely wrong pw")
            return time.perf_counter() - start

        known = min(timed("alice") for _ in range(3))
        unknown = min(timed("ghost") for _ in range(3))
        assert unknown > known * 0.5, (known, unknown)


def test_disabled_account_cannot_log_in(app, client, make_user):
    bob = make_user("bob")
    bob_id = bob.get("/api/auth/me").json()["id"]
    assert client.patch(f"/api/admin/users/{bob_id}", json={"active": False}).json()["active"] is False
    assert bob.get("/api/conversations").status_code == 401  # sessions revoked
    c = new_client(app)
    assert code(login(c, "bob")) == "account_disabled"


def test_rate_limiting_locks_account_then_recovers(app, client):
    c = new_client(app)
    for _ in range(5):
        assert login(c, "alice", "wrong password!!").status_code == 401
    locked = login(c, "alice", PASSWORD)  # even the right password is refused while locked
    assert locked.status_code == 429 and code(locked) == "rate_limited"
    assert int(locked.headers["retry-after"]) >= 1
    state = app.state.chimera
    state.db.execute("UPDATE auth_throttle SET locked_until = 0")
    assert login(c, "alice", PASSWORD).status_code == 200


def test_per_ip_limit_spans_accounts(app, client):
    c = new_client(app)
    limit = app.state.chimera.config.login_max_failures * 4
    for i in range(limit):
        login(c, f"user{i}", "wrong password!!")
    assert code(login(c, "someone-else", "wrong password!!")) == "rate_limited"


def test_register_endpoint_is_rate_limited(app, client):
    client.patch("/api/admin/settings", json={"registration_mode": "open"})
    app.state.chimera.config.register_per_hour = 3
    c = new_client(app)
    codes = [register(c, f"user{i}", password="x").status_code for i in range(4)]
    assert codes[-1] == 429


# ----------------------------------------------------------------- sessions
def test_session_cookie_flags_and_storage(app, anon):
    resp = register(anon, "alice")
    cookies = [v for k, v in resp.headers.multi_items() if k == "set-cookie"]
    session = next(c for c in cookies if c.startswith("chimera_session="))
    csrf = next(c for c in cookies if c.startswith("chimera_csrf="))
    assert "httponly" in session.lower() and "samesite=lax" in session.lower()
    assert "httponly" not in csrf.lower()
    token = anon.cookies.get("chimera_session")
    rows = app.state.chimera.db.all("SELECT token_hash FROM sessions")
    assert len(rows) == 1 and token not in rows[0]["token_hash"]  # only a hash is stored


def test_secure_cookie_behind_trusted_https_proxy(tmp_path):
    app = create_app(make_config(tmp_path, trusted_proxies=["127.0.0.0/8", "testclient"]))
    with CsrfClient(app, base_url="http://localhost", client=("127.0.0.1", 5000)) as c:
        c.get("/api/auth/status")
        resp = register(c, "alice", headers={"X-Forwarded-Proto": "https"})
        session = next(v for k, v in resp.headers.multi_items() if k == "set-cookie" and v.startswith("chimera_session"))
        assert "secure" in session.lower()


def test_logout_invalidates_session(app, client):
    token = client.cookies.get("chimera_session")
    client.post("/api/auth/logout")
    other = CsrfClient(app, base_url="http://localhost", cookies={"chimera_session": token})
    assert other.get("/api/conversations").status_code == 401


def _age_session(app, *, last_used: timedelta | None = None, expires: timedelta | None = None):
    now = datetime.now(timezone.utc)
    db = app.state.chimera.db
    if last_used is not None:
        db.execute("UPDATE sessions SET last_used_at = ?", ((now - last_used).isoformat(),))
    if expires is not None:
        db.execute("UPDATE sessions SET expires_at = ?", ((now + expires).isoformat(),))


def test_idle_timeout(app, client):
    _age_session(app, last_used=timedelta(hours=13))
    assert client.get("/api/conversations").status_code == 401


def test_absolute_expiry(app, client):
    _age_session(app, expires=timedelta(seconds=-1))
    assert client.get("/api/conversations").status_code == 401


def test_remember_me_extends_idle_window(app, client):
    c = new_client(app)
    resp = login(c, "alice", remember=True)
    assert "max-age" in next(v for k, v in resp.headers.multi_items() if k == "set-cookie" and v.startswith("chimera_session")).lower()
    app.state.chimera.db.execute(
        "UPDATE sessions SET last_used_at = ? WHERE remember = 1", ((datetime.now(timezone.utc) - timedelta(days=3)).isoformat(),)
    )
    assert c.get("/api/conversations").status_code == 200


def test_login_rotates_session(app, client):
    before = client.cookies.get("chimera_session")
    login(client, "alice")
    after = client.cookies.get("chimera_session")
    assert before != after
    stale = CsrfClient(app, base_url="http://localhost", cookies={"chimera_session": before})
    assert stale.get("/api/conversations").status_code == 401


def test_password_change_revokes_other_sessions(app, client):
    second = new_client(app)
    login(second, "alice")
    new_pw = "a brand new passphrase"
    resp = client.post("/api/account/password", json={"current_password": PASSWORD, "new_password": new_pw, "new_password_confirm": new_pw})
    assert resp.status_code == 200
    assert client.get("/api/conversations").status_code == 200
    assert second.get("/api/conversations").status_code == 401
    assert login(new_client(app), "alice", new_pw).status_code == 200
    bad = client.post("/api/account/password", json={"current_password": "nope nope nope", "new_password": new_pw, "new_password_confirm": new_pw})
    assert code(bad) == "current_password_incorrect"


def test_sessions_list_and_revoke(app, client):
    second = new_client(app)
    login(second, "alice")
    sessions = client.get("/api/account/sessions").json()
    assert len(sessions) == 2 and sum(s["current"] for s in sessions) == 1
    other = next(s for s in sessions if not s["current"])
    assert client.delete(f"/api/account/sessions/{other['id']}").status_code == 204
    assert second.get("/api/conversations").status_code == 401
    assert client.delete("/api/account/sessions/doesnotexist").status_code == 404


def test_csrf_required_for_state_changes(app, client):
    client.send_csrf = False
    resp = client.post("/api/conversations", json={})
    assert resp.status_code == 403 and code(resp) == "csrf_failed"
    resp = client.post("/api/conversations", json={}, headers={"X-CSRF-Token": "forged"})
    assert code(resp) == "csrf_failed"
    # An attacker can't reuse an anonymous token against a logged-in session either.
    anon = new_client(app)
    resp = client.post("/api/conversations", json={}, headers={"X-CSRF-Token": anon.cookies["chimera_csrf"]})
    assert code(resp) == "csrf_failed"
    client.send_csrf = True
    assert client.post("/api/conversations", json={}).status_code == 201


def test_csrf_on_login_form(app, client):
    c = new_client(app)
    c.send_csrf = False
    assert code(login(c, "alice")) == "csrf_failed"


# ------------------------------------------------------- admin & last admin
def test_admin_reset_password_forces_change(app, client, make_user):
    bob = make_user("bob")
    bob_id = bob.get("/api/auth/me").json()["id"]
    temp = client.post(f"/api/admin/users/{bob_id}/reset-password").json()["temporary_password"]
    assert bob.get("/api/conversations").status_code == 401  # old sessions gone
    c = new_client(app)
    me = login(c, "bob", temp).json()
    assert me["must_change_password"] is True
    assert code(c.get("/api/conversations")) == "password_change_required"
    new_pw = "a fresh long passphrase"
    c.post("/api/account/password", json={"current_password": temp, "new_password": new_pw, "new_password_confirm": new_pw})
    assert c.get("/api/conversations").status_code == 200


def test_admin_create_user_with_temporary_password(app, client):
    resp = client.post("/api/admin/users", json={"email": "c@example.com", "username": "carol"}).json()
    assert resp["temporary_password"] and resp["must_change_password"] is True


def test_non_admin_cannot_use_admin_endpoints(make_user):
    bob = make_user("bob")
    assert bob.get("/api/admin/users").status_code == 404
    assert bob.patch("/api/admin/settings", json={"registration_mode": "open"}).status_code == 404
    assert bob.get("/api/data/backup").status_code == 404


def test_last_admin_protection(app, client, make_user):
    me = client.get("/api/auth/me").json()
    assert code(client.patch(f"/api/admin/users/{me['id']}", json={"role": "user"})) == "last_admin"
    assert code(client.patch(f"/api/admin/users/{me['id']}", json={"active": False})) == "last_admin"
    assert code(client.request("DELETE", "/api/account", json={"password": PASSWORD})) == "last_admin"
    bob = make_user("bob")
    bob_id = bob.get("/api/auth/me").json()["id"]
    client.patch(f"/api/admin/users/{bob_id}", json={"role": "admin"})
    assert client.patch(f"/api/admin/users/{me['id']}", json={"role": "user"}).status_code == 200


def test_profile_update_requires_password_for_identity_changes(client):
    assert code(client.patch("/api/account", json={"email": "new@example.com"})) == "current_password_incorrect"
    resp = client.patch("/api/account", json={"email": "new@example.com", "current_password": PASSWORD})
    assert resp.json()["email"] == "new@example.com"
    assert client.patch("/api/account", json={"language": "lt"}).json()["language"] == "lt"
    assert client.patch("/api/account", json={"language": "de"}).status_code == 422


def test_cli_reset_password(app, client, config, monkeypatch, capsys):
    from chimera import cli

    monkeypatch.setenv("CHIMERA_DATA_DIR", str(config.data_dir))
    monkeypatch.setenv("CHIMERA_ARGON2_MEMORY_KIB", "1024")
    monkeypatch.setenv("CHIMERA_ARGON2_TIME_COST", "1")
    monkeypatch.setenv("CHIMERA_ARGON2_PARALLELISM", "1")
    monkeypatch.setenv("CHIMERA_ENV_FILE", "/nonexistent")
    cli.main(["reset-password", "alice"])
    temp = capsys.readouterr().out.split("alice: ")[1].split()[0]
    assert client.get("/api/conversations").status_code == 401
    c = new_client(app)
    assert login(c, "alice", temp).json()["must_change_password"] is True
