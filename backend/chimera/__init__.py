"""Chimera — a local-first AI chat workspace.

The version has one source of truth: ``backend/pyproject.toml``. A source
checkout reads it from there; an installed package (the Docker image) reads
the same value from the package metadata that pip wrote from that file.
``scripts/check-version.py`` keeps every other place (frontend/package.json,
docker-compose.yml, CHANGELOG.md, release tags) consistent with it.
"""

from __future__ import annotations

import os
import re
from pathlib import Path


def _read_version() -> str:
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    if pyproject.is_file():
        match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject.read_text(encoding="utf-8"), re.M)
        if match:
            return match.group(1)
    try:
        from importlib.metadata import PackageNotFoundError, version

        return semver_from_pep440(version("chimera"))
    except PackageNotFoundError:  # pragma: no cover - neither a checkout nor installed
        return "0.0.0+unknown"


def semver_from_pep440(value: str) -> str:
    """pip stores ``1.0.0-rc.1`` as ``1.0.0rc1`` (PEP 440); turn it back."""
    match = re.fullmatch(r"(\d+\.\d+\.\d+)(a|b|rc)(\d+)", value)
    if not match:
        return value
    label = {"a": "alpha", "b": "beta", "rc": "rc"}[match.group(2)]
    return f"{match.group(1)}-{label}.{match.group(3)}"


__version__ = _read_version()
# Set when the image is built (Dockerfile ARG); empty for source checkouts.
BUILD_COMMIT = os.environ.get("CHIMERA_BUILD_COMMIT", "").strip()[:40]
