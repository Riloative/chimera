"""Release engineering: version, upgrades from real 0.3.0/0.4.0 databases, the
downgrade guard, backup/restore, startup validation, JSON logs, shutdown."""

from __future__ import annotations

import io
import json
import logging
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest

from chimera import __version__
from chimera.config import Config, validate_config
from chimera.db import SCHEMA_VERSION, Database, SchemaTooNew
from chimera.main import create_app
from chimera.maintenance import DataDirLock, MaintenanceError, backup, inspect_archive, restore

from .conftest import PASSWORD, CsrfClient, login, make_config, read_sse

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures" / "databases"
FIXTURE_KEY = "Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYmFyYmE="


# ---------------------------------------------------------------- version
def test_version_has_one_source_of_truth():
    pyproject = (ROOT / "backend" / "pyproject.toml").read_text()
    assert re.search(rf'^version = "{re.escape(__version__)}"$', pyproject, re.M)
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "check-version.py")], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_installed_package_versions_map_back_to_semver():
    from chimera import semver_from_pep440

    assert semver_from_pep440("1.0.0rc1") == "1.0.0-rc.1"
    assert semver_from_pep440("2.1.0b3") == "2.1.0-beta.3"
    assert semver_from_pep440("1.0.0") == "1.0.0"


def test_version_check_catches_a_mismatch(tmp_path):
    for rel in ("backend/pyproject.toml", "frontend/package.json", "docker-compose.yml", "CHANGELOG.md"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, tmp_path / rel)
    pkg = json.loads((tmp_path / "frontend/package.json").read_text())
    pkg["version"] = "9.9.9"
    (tmp_path / "frontend/package.json").write_text(json.dumps(pkg))
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "check-version.py"), "--root", str(tmp_path)],
                          capture_output=True, text=True)
    assert proc.returncode == 1 and "frontend/package.json" in proc.stdout
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "check-version.py"), "--tag", "v0.0.1"],
                          capture_output=True, text=True)
    assert proc.returncode == 1 and "tag" in proc.stdout


# --------------------------------------------------------------- upgrades
@pytest.mark.parametrize("version", ["0.3.0", "0.4.0"])
def test_upgrade_from_a_real_older_database(tmp_path, version):
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(FIXTURES / f"chimera-{version}.sqlite", data / "chimera.sqlite")
    cfg = make_config(tmp_path, encryption_key=FIXTURE_KEY)
    with CsrfClient(create_app(cfg), base_url="http://localhost") as c:
        c.get("/api/auth/status")
        assert login(c, "fixadmin").status_code == 200
        [conv] = [x for x in c.get("/api/conversations").json() if x["title"] == "Fixture conversation"]
        detail = c.get(f"/api/conversations/{conv['id']}").json()
        path = [m for m in detail["messages"] if m["id"] in detail["active_path"]]
        assert [m["content"] for m in path if m["role"] == "user"] == ["First question about the fixture", "Second question"]
        assert [s["url"] for s in detail["web_sources"]] == ["https://example.org/page"]
        assert any(p["title"] == "Fixture prompt" for p in c.get("/api/prompts").json())
        assert c.get("/api/ready").json()["status"] == "ready"
        state = c.app.state.chimera
        assert state.db.schema_version() == SCHEMA_VERSION
    backups = list((data / "backups").glob("pre-migration-*.sqlite"))
    if version == "0.3.0":
        assert len(backups) == 1 and sqlite3.connect(backups[0]).execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 3
    else:
        assert backups == []  # 0.4.0 already has the current schema: nothing to migrate


# --------------------------------------------------------- downgrade guard
def newer_db(path: Path) -> None:
    Database(path).close()
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION + 1,))
    conn.commit()
    conn.close()


def test_downgrade_guard_refuses_a_newer_database(tmp_path):
    db = tmp_path / "chimera.sqlite"
    newer_db(db)
    before = db.read_bytes()
    with pytest.raises(SchemaTooNew) as err:
        Database(db, backups_dir=tmp_path / "backups")
    assert "newer Chimera" in str(err.value) and "pre-migration" in str(err.value)
    assert db.read_bytes() == before  # untouched


def test_server_refuses_to_start_on_a_newer_database(tmp_path):
    data = tmp_path / "data"
    newer_db(data / "chimera.sqlite") if data.mkdir() is None else None
    env = {"CHIMERA_DATA_DIR": str(data), "CHIMERA_ENV_FILE": "/nonexistent", "PATH": "/usr/bin:/bin"}
    proc = subprocess.run([sys.executable, "-m", "chimera", "--port", "0"], cwd=ROOT / "backend", env=env,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 3
    assert "only supports up to" in proc.stderr


def test_restoring_a_newer_backup_in_the_ui_is_refused(client, tmp_path):
    db = tmp_path / "newer.sqlite"
    newer_db(db)
    resp = client.post("/api/data/restore", files={"file": ("b.sqlite", db.read_bytes(), "application/octet-stream")})
    assert resp.status_code == 422 and resp.json()["detail"]["code"] == "restore_too_new"
    assert client.get("/api/ready").json()["status"] == "ready"  # the live database is untouched


# ---------------------------------------------------------- backup/restore
def populated(tmp_path: Path) -> tuple[Config, str]:
    cfg = make_config(tmp_path)
    with CsrfClient(create_app(cfg), base_url="http://localhost") as c:
        c.get("/api/auth/status")
        from .conftest import register

        assert register(c, "owner").status_code == 201
        conv = c.post("/api/conversations", json={"title": "Keep me"}).json()
        png = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
               b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa75\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82")
        up = c.post("/api/files", files={"file": ("pixel.png", png, "image/png")})
        assert up.status_code in (200, 201), up.text
    return cfg, conv["id"]


def test_backup_and_restore_round_trip(tmp_path):
    cfg, cid = populated(tmp_path)
    archive = Path(backup(cfg.data_dir, tmp_path / "out")["path"])
    assert archive.stat().st_mode & 0o077 == 0  # contains keys: owner-only
    assert (tmp_path / "out").stat().st_mode & 0o777 == 0o700
    manifest = inspect_archive(archive)
    assert manifest["chimera_version"] == __version__ and manifest["schema_version"] == SCHEMA_VERSION
    names = tarfile.open(archive).getnames()
    assert "chimera.sqlite" in names and "keys/.encryption_key" in names and any(n.startswith("uploads/") for n in names)
    key = (cfg.data_dir / ".encryption_key").read_text()

    # Lose everything, then restore.
    (cfg.data_dir / "chimera.sqlite").unlink()
    (cfg.data_dir / ".encryption_key").write_text("something else")
    shutil.rmtree(cfg.data_dir / "uploads")
    result = restore(cfg.data_dir, archive)
    assert (cfg.data_dir / ".encryption_key").read_text() == key
    assert Path(result["previous_data"], ".encryption_key").read_text() == "something else"  # kept aside
    assert any((cfg.data_dir / "uploads").rglob("*"))
    with CsrfClient(create_app(cfg), base_url="http://localhost") as c:
        c.get("/api/auth/status")
        assert login(c, "owner").status_code == 200
        assert c.get(f"/api/conversations/{cid}").json()["title"] == "Keep me"


def test_restore_refuses_while_the_server_runs(tmp_path):
    cfg, _ = populated(tmp_path)
    archive = Path(backup(cfg.data_dir, tmp_path / "b.tar.gz")["path"])
    with CsrfClient(create_app(cfg), base_url="http://localhost"):
        with pytest.raises(MaintenanceError, match="running"):
            restore(cfg.data_dir, archive)
    restore(cfg.data_dir, archive)  # fine once stopped


def test_two_servers_cannot_share_a_data_directory(tmp_path):
    cfg = make_config(tmp_path)
    lock = DataDirLock(cfg.data_dir)
    assert lock.acquire()
    try:
        with pytest.raises(RuntimeError, match="already using"):
            with CsrfClient(create_app(cfg), base_url="http://localhost"):
                pass
    finally:
        lock.release()


def _archive(tmp_path: Path, members: dict[str, bytes], manifest: dict | None = None) -> Path:
    path = tmp_path / "evil.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for name, data in {"manifest.json": json.dumps(manifest or {"format": "chimera-backup"}).encode(), **members}.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


def test_restore_rejects_unsafe_or_newer_archives(tmp_path):
    cfg, _ = populated(tmp_path)
    with pytest.raises(MaintenanceError, match="Unsafe path"):
        restore(cfg.data_dir, _archive(tmp_path, {"../escape": b"x"}))
    with pytest.raises(MaintenanceError, match="Unexpected file"):
        restore(cfg.data_dir, _archive(tmp_path, {"etc/passwd": b"x"}))
    with pytest.raises(MaintenanceError, match="Not a Chimera backup"):
        restore(cfg.data_dir, _archive(tmp_path, {}, {"format": "other"}))
    newer = tmp_path / "newer.sqlite"
    newer_db(newer)
    with pytest.raises(MaintenanceError, match="newer Chimera"):
        restore(cfg.data_dir, _archive(tmp_path, {"chimera.sqlite": newer.read_bytes()}))
    assert (cfg.data_dir / "chimera.sqlite").exists()  # nothing was replaced


def test_cli_backup_and_restore(tmp_path):
    cfg, _ = populated(tmp_path)
    env = {"CHIMERA_DATA_DIR": str(cfg.data_dir), "CHIMERA_ENV_FILE": "/nonexistent", "PATH": "/usr/bin:/bin"}
    run = lambda *a: subprocess.run([sys.executable, "-m", "chimera", *a], cwd=ROOT / "backend", env=env,  # noqa: E731
                                    capture_output=True, text=True, timeout=60)
    out = run("backup", str(tmp_path / "cli"))
    assert out.returncode == 0 and "Backup written" in out.stdout
    archive = next((tmp_path / "cli").glob("chimera-backup-*.tar.gz"))
    refused = run("restore", str(archive))
    assert refused.returncode != 0 and "--yes" in refused.stderr
    done = run("restore", str(archive), "--yes")
    assert done.returncode == 0 and "Restored" in done.stdout
    assert run("version").stdout.startswith(f"Chimera {__version__}")


# ------------------------------------------------------------ validation
@pytest.mark.parametrize(("overrides", "fragment"), [
    ({"browser_url": "http://browser:3000", "browser_secret": ""}, "CHIMERA_BROWSER_SECRET is missing"),
    ({"trusted_proxies": ["172.18.0.1", "not-an-ip"]}, "CHIMERA_TRUSTED_PROXIES contains 'not-an-ip'"),
    ({"port": 70000}, "not a valid port"),
    ({"log_format": "xml"}, "CHIMERA_LOG_FORMAT"),
    ({"log_level": "loud"}, "CHIMERA_LOG_LEVEL"),
    ({"encryption_key": "short"}, "CHIMERA_ENCRYPTION_KEY"),
    ({"update_check_url": "http://example.org"}, "https://"),
])
def test_invalid_configuration_is_fatal(overrides, fragment):
    errors, _ = validate_config(Config(**overrides))
    assert any(fragment in e for e in errors), errors


@pytest.mark.parametrize(("overrides", "fragment"), [
    ({"allowed_hosts": ["*"], "registration_mode": "open"}, "open registration"),
    ({"cookie_secure": "true"}, "signing in over plain HTTP will fail"),
    ({"host": "0.0.0.0", "registration_mode": "open"}, "CHIMERA_SETUP_TOKEN"),
])
def test_risky_configuration_warns(overrides, fragment):
    errors, warnings = validate_config(Config(**overrides))
    assert not errors and any(fragment in w for w in warnings), warnings


def test_default_configuration_is_clean():
    assert validate_config(Config()) == ([], [])


def test_server_exits_on_invalid_configuration(tmp_path):
    env = {"CHIMERA_DATA_DIR": str(tmp_path), "CHIMERA_ENV_FILE": "/nonexistent", "PATH": "/usr/bin:/bin",
           "CHIMERA_BROWSER_URL": "http://browser:3000"}
    proc = subprocess.run([sys.executable, "-m", "chimera", "--check-config"], cwd=ROOT / "backend", env=env,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 2 and "CHIMERA_BROWSER_SECRET" in proc.stderr and "Not starting" in proc.stderr


# --------------------------------------------------------------- logging
def test_json_logs_have_fixed_fields_and_no_secrets(tmp_path, capsys):
    from chimera.logs import configure_logging

    configure_logging("info", "json")
    try:
        cfg = make_config(tmp_path)
        secret_text = "my very private question 7c1e"
        with CsrfClient(create_app(cfg), base_url="http://localhost") as c:
            c.get("/api/auth/status")
            from .conftest import register

            register(c, "jsonuser")
            c.post("/api/auth/logout")
            login(c, "jsonuser")
            conv = c.post("/api/conversations", json={}).json()
            with c.stream("POST", f"/api/conversations/{conv['id']}/messages", json={"content": secret_text}) as r:
                read_sse(r)
        logging.getLogger("chimera.test").info("hello %s", "world", extra={"password": "leak", "token": "leak"})
        access = logging.getLogger("uvicorn.access")
        access.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:1", "GET", "/api/conversations/search?q=private+words", "1.1", 200)
    finally:
        logging.config.dictConfig({"version": 1, "disable_existing_loggers": False})
    captured = capsys.readouterr()
    lines = [json.loads(line) for line in (captured.err + captured.out).splitlines() if line.startswith("{")]
    assert lines and all(set(line) <= {"time", "level", "logger", "message", "exception"} for line in lines)
    blob = captured.err + captured.out
    assert PASSWORD not in blob and secret_text not in blob and "leak" not in blob and "private+words" not in blob
    assert any(line["message"] == "hello world" for line in lines)
    assert any("/api/conversations/search " in line["message"] for line in lines)


# -------------------------------------------------------------- shutdown
def test_shutdown_marks_running_answers_interrupted(client, conversation, mock):
    import threading

    box = {}

    def consume():
        with client.stream("POST", f"/api/conversations/{conversation['id']}/messages", json={"content": "[slow] long"}) as resp:
            box["events"] = read_sse(resp)

    thread = threading.Thread(target=consume)
    thread.start()
    state = client.app.state.chimera
    deadline = time.time() + 10
    while not state.chat.active and time.time() < deadline:
        time.sleep(0.02)
    client.portal.call(state.chat.shutdown, 0.05)
    thread.join(timeout=10)
    msg = next(d for e, d in box["events"] if e in ("done", "error"))["message"]
    assert msg["status"] == "interrupted" and msg["error_code"] == "interrupted"


def test_shutdown_lets_short_answers_finish(client, conversation):
    import threading

    box = {}

    def consume():
        with client.stream("POST", f"/api/conversations/{conversation['id']}/messages", json={"content": "quick"}) as resp:
            box["events"] = read_sse(resp)

    thread = threading.Thread(target=consume)
    thread.start()
    state = client.app.state.chimera
    deadline = time.time() + 10
    while not state.chat.active and time.time() < deadline:
        time.sleep(0.01)
    client.portal.call(state.chat.shutdown, 10)
    thread.join(timeout=10)
    msg = next(d for e, d in box["events"] if e in ("done", "error"))["message"]
    assert msg["status"] == "complete"
