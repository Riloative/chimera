#!/usr/bin/env python3
"""Check that every place carrying Chimera's version agrees with the single
source of truth, ``backend/pyproject.toml``.

    scripts/check-version.py               # CI on every pull request
    scripts/check-version.py --tag v1.2.3  # release workflow: tag must match too

Checked: frontend/package.json (+ package-lock.json), the default image tag in
docker-compose.yml, and a CHANGELOG.md section for the version. Exit code 1
with one line per mismatch.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument("--tag", help="release tag to check, e.g. v1.0.0")
    args = parser.parse_args()
    root = Path(args.root)
    problems: list[str] = []

    match = re.search(r'^version\s*=\s*"([^"]+)"', (root / "backend/pyproject.toml").read_text(), re.M)
    if not match:
        print("backend/pyproject.toml: no version")
        return 1
    version = match.group(1)
    if not SEMVER.match(version):
        problems.append(f"backend/pyproject.toml: {version!r} is not a semantic version")

    pkg = json.loads((root / "frontend/package.json").read_text())
    if pkg.get("version") != version:
        problems.append(f"frontend/package.json: version {pkg.get('version')!r} != {version!r}")
    lock = root / "frontend/package-lock.json"
    if lock.is_file():
        data = json.loads(lock.read_text())
        for where, value in (("version", data.get("version")), ('packages[""].version', data.get("packages", {}).get("", {}).get("version"))):
            if value != version:
                problems.append(f"frontend/package-lock.json: {where} {value!r} != {version!r}")

    compose = (root / "docker-compose.yml").read_text()
    tags = re.findall(r"image:\s*ghcr\.io/riloative/chimera(?:-browser)?:\$\{CHIMERA_VERSION:-([^}]+)\}", compose)
    if len(tags) < 2:
        problems.append("docker-compose.yml: expected ghcr.io/riloative/chimera and chimera-browser images with ${CHIMERA_VERSION:-<version>}")
    for tag in tags:
        if tag != version:
            problems.append(f"docker-compose.yml: default image tag {tag!r} != {version!r}")

    changelog = (root / "CHANGELOG.md").read_text()
    if not re.search(rf"^## \[{re.escape(version)}\]", changelog, re.M):
        problems.append(f"CHANGELOG.md: no '## [{version}]' section")

    if args.tag is not None and args.tag != f"v{version}":
        problems.append(f"release tag {args.tag!r} != 'v{version}'")

    for line in problems:
        print(line)
    if not problems:
        print(f"version {version}: consistent")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
