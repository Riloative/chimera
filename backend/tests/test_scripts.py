"""scripts/install-docker.sh and scripts/update.sh against a fake `docker`
(tests/fake_docker.sh) and a fake release server (file:// URLs)."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


class Env:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.state = tmp / "state"
        self.state.mkdir()
        (self.state / "volume").write_text("data-v1\n")
        self.bin = tmp / "bin"
        self.bin.mkdir()
        docker = self.bin / "docker"
        shutil.copy(Path(__file__).parent / "fake_docker.sh", docker)
        docker.chmod(0o755)
        self.releases = tmp / "releases"
        self.releases.mkdir()
        self.dir = tmp / "chimera"

    def release(self, version: str, compose_extra: str = "") -> None:
        d = self.releases / f"v{version}"
        d.mkdir()
        compose = (ROOT / "docker-compose.yml").read_text().replace("1.0.0-rc.1", version) + compose_extra
        files = {
            "docker-compose.yml": compose,
            ".env.example": (ROOT / ".env.example").read_text(),
            "searxng-settings.yml": (ROOT / "deploy/searxng/settings.yml").read_text(),
            "update.sh": (ROOT / "scripts/update.sh").read_text(),
            "chimera-update.service": (ROOT / "deploy/systemd/chimera-update.service").read_text(),
            "chimera-update.timer": (ROOT / "deploy/systemd/chimera-update.timer").read_text(),
        }
        sums = []
        for name, text in files.items():
            (d / name).write_text(text)
            sums.append(f"{hashlib.sha256(text.encode()).hexdigest()}  {name}")
        (d / "SHA256SUMS").write_text("\n".join(sums) + "\n")

    def releases_api(self, versions: list[tuple[str, bool]]) -> str:
        path = self.tmp / "releases.json"
        path.write_text(json.dumps([{"tag_name": f"v{v}", "prerelease": pre, "draft": False} for v, pre in versions]))
        latest = self.tmp / "latest.json"
        stable = [v for v, pre in versions if not pre]
        latest.write_text(json.dumps({"tag_name": f"v{stable[0]}"}) if stable else "{}")
        return path.as_uri()

    def run(self, script: str, *args: str, **env: str) -> subprocess.CompletedProcess:
        full = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.tmp),
            "FAKE_STATE": str(self.state),
            "CHIMERA_RELEASE_BASE": self.releases.as_uri(),
            "CHIMERA_RELEASES_API": (self.tmp / "releases.json").as_uri(),
            "CHIMERA_MIN_FREE_MB": "1",
            "CHIMERA_HEALTH_TIMEOUT": "6",
            **env,
        }
        return subprocess.run(["bash", script, *args], env=full, capture_output=True, text=True, timeout=120,
                              stdin=subprocess.DEVNULL)

    def calls(self) -> list[str]:
        path = self.state / "calls.log"
        return path.read_text().splitlines() if path.exists() else []

    def install(self, version: str = "1.0.0", *extra: str) -> subprocess.CompletedProcess:
        return self.run(str(ROOT / "scripts/install-docker.sh"), "--dir", str(self.dir), "--version", version, "--yes", *extra)

    def update(self, *args: str, **env: str) -> subprocess.CompletedProcess:
        return self.run(str(self.dir / "update.sh"), "--dir", str(self.dir), "--timeout", "6", *args, **env)

    def env_file(self) -> str:
        return (self.dir / ".env").read_text()


@pytest.fixture()
def env(tmp_path):
    e = Env(tmp_path)
    for v in ("1.0.0", "1.0.1", "1.1.0", "1.2.0-rc.1", "2.0.0"):
        e.release(v)
    e.releases_api([("2.0.0", False), ("1.2.0-rc.1", True), ("1.1.0", False), ("1.0.1", False), ("1.0.0", False)])
    return e


# ---------------------------------------------------------------- install
def test_install_happy_path(env):
    out = env.install("1.0.0", "--with", "searxng,browser")
    assert out.returncode == 0, out.stderr + out.stdout
    dotenv = env.env_file()
    assert "CHIMERA_VERSION=1.0.0" in dotenv and "COMPOSE_PROFILES=searxng,browser" in dotenv
    assert "CHIMERA_BROWSER_URL=http://browser:3000" in dotenv
    for key in ("SEARXNG_SECRET", "CHIMERA_BROWSER_SECRET", "CHIMERA_SETUP_TOKEN"):
        value = next(line.split("=", 1)[1] for line in dotenv.splitlines() if line.startswith(key + "="))
        assert len(value) >= 32
    assert stat.S_IMODE((env.dir / ".env").stat().st_mode) == 0o600
    assert os.access(env.dir / "update.sh", os.X_OK)
    assert (env.dir / "deploy/searxng/settings.yml").is_file()
    assert "ghcr.io/riloative/chimera:${CHIMERA_VERSION:-1.0.0}" in (env.dir / "docker-compose.yml").read_text()
    calls = env.calls()
    assert "docker compose pull" in calls and "docker compose up -d" in calls
    assert "is running: http://127.0.0.1:8700" in out.stdout and "Setup token" in out.stdout


def test_install_is_idempotent_and_never_overwrites_env(env):
    assert env.install("1.0.0").returncode == 0
    (env.dir / ".env").write_text((env.dir / ".env").read_text() + "MY_SETTING=keep\n")
    before = env.env_file()
    out = env.install("1.0.0", "--with", "browser")
    assert out.returncode == 0, out.stderr
    assert env.env_file() == before and ".env exists: leaving it unchanged" in out.stdout
    assert not list(env.dir.glob("docker-compose.yml.bak-*"))  # identical file: no backup copy


def test_install_refuses_a_checksum_mismatch(env):
    (env.releases / "v1.0.0" / "docker-compose.yml").write_text("tampered: true\n")
    out = env.install("1.0.0")
    assert out.returncode != 0 and "Checksum mismatch" in out.stderr
    assert not (env.dir / ".env").exists()


def test_install_resolves_the_latest_release(env):
    out = env.run(str(ROOT / "scripts/install-docker.sh"), "--dir", str(env.dir), "--yes", "--no-start",
                  CHIMERA_RELEASES_API=(env.tmp / "latest.json").as_uri())
    assert out.returncode == 0, out.stderr
    assert "CHIMERA_VERSION=2.0.0" in env.env_file()


# ----------------------------------------------------------------- update
@pytest.fixture()
def installed(env):
    assert env.install("1.0.0").returncode == 0
    (env.state / "calls.log").unlink()
    return env


def test_update_happy_path_within_the_major_line(installed):
    env = installed
    out = env.update("--yes")
    assert out.returncode == 0, out.stderr
    assert "CHIMERA_VERSION=1.1.0" in env.env_file()  # newest 1.x stable (not 2.0.0, not the rc)
    [backup] = list((env.dir / "backups").glob("update-*-from-1.0.0"))
    assert (backup / "data-volume.tar.gz").read_text() == "VOLUME:data-v1"
    assert "CHIMERA_VERSION=1.0.0" in (backup / ".env").read_text()
    assert stat.S_IMODE((backup / ".env").stat().st_mode) == 0o600
    calls = env.calls()
    order = [c for c in calls if c.startswith(("docker compose -f", "docker compose stop", "docker compose up", "docker image rm"))]
    assert order[0].endswith(" pull") and "stop chimera" in order[1] and order[2] == "docker compose up -d"
    assert "docker image rm ghcr.io/riloative/chimera:1.0.0" in calls
    assert not any("volume rm" in c or "prune" in c for c in calls)
    assert "1.0.0 → 1.1.0" in (env.dir / "update.log").read_text()


def test_patch_channel_stays_on_the_minor_line(installed):
    out = installed.update("--yes", "--channel", "patch")
    assert out.returncode == 0, out.stderr
    assert "CHIMERA_VERSION=1.0.1" in installed.env_file()


def test_a_major_version_needs_allow_major(installed):
    env = installed
    refused = env.update("--yes", "--version", "2.0.0")
    assert refused.returncode == 1 and "--allow-major" in refused.stderr
    assert "CHIMERA_VERSION=1.0.0" in env.env_file() and "docker compose up -d" not in env.calls()
    allowed = env.update("--yes", "--version", "2.0.0", "--allow-major")
    assert allowed.returncode == 0, allowed.stderr
    assert "CHIMERA_VERSION=2.0.0" in env.env_file()


def test_failed_health_check_rolls_back_files_data_and_version(installed):
    env = installed
    out = env.update("--yes", FAKE_BAD_VERSION="1.1.0")
    assert out.returncode == 1
    assert "rolling back to 1.0.0" in out.stderr and "Rolled back" in out.stderr
    assert "CHIMERA_VERSION=1.0.0" in env.env_file()
    assert "1.0.0" in (env.dir / "docker-compose.yml").read_text()
    assert (env.state / "volume").read_text().strip() == "data-v1"  # the "migration" was undone
    assert (env.state / "version").read_text().strip() == "1.0.0" and (env.state / "running").read_text().strip() == "1"
    assert not any(c.startswith("docker image rm") for c in env.calls())  # old images kept


def test_a_crash_looping_release_is_rolled_back_without_waiting_for_the_timeout(installed):
    import time

    started = time.monotonic()
    out = installed.update("--yes", "--timeout", "60", FAKE_BAD_VERSION="1.1.0", FAKE_BAD_STATE="restarting", FAKE_BAD_RESTARTS="3")
    assert out.returncode == 1 and "Rolled back" in out.stderr and "restarts: 3" in out.stderr
    assert time.monotonic() - started < 30


def test_dry_run_changes_nothing(installed):
    env = installed
    before = env.env_file()
    out = env.update("--dry-run")
    assert out.returncode == 0 and "Plan: Chimera 1.0.0 → 1.1.0" in out.stderr and "Dry run" in out.stderr
    assert env.env_file() == before
    assert not any(word in c for c in env.calls() for word in (" pull", " up ", " up -d", " stop", " run "))
    assert not list((env.dir / "backups").glob("update-*"))


def test_failed_pull_changes_nothing(installed):
    env = installed
    before = env.env_file()
    out = env.update("--yes", FAKE_PULL_FAIL="1")
    assert out.returncode == 1 and "nothing was changed" in out.stderr
    assert env.env_file() == before and not any("stop" in c for c in env.calls())


def test_downgrades_and_up_to_date(installed):
    env = installed
    assert "Downgrades are not supported" in env.update("--yes", "--version", "0.9.0").stderr
    assert "already installed" in env.update("--yes", "--version", "1.0.0").stderr
    env.update("--yes")
    assert "up to date" in env.update("--yes").stderr


def test_updating_from_an_image_without_the_backup_command(installed):
    out = installed.update("--yes", FAKE_NO_BACKUP_CLI="1")  # e.g. a 0.x image
    assert out.returncode == 0, out.stderr
    [backup] = list((installed.dir / "backups").glob("update-*"))
    assert (backup / "data-volume.tar.gz").read_text() == "VOLUME:data-v1"


def test_non_interactive_update_needs_yes(installed):
    out = installed.update()
    assert out.returncode == 1 and "--yes" in out.stderr
