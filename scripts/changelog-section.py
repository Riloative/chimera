#!/usr/bin/env python3
"""Print the CHANGELOG.md section for a version (release notes).

    scripts/changelog-section.py 1.2.0
    scripts/changelog-section.py 1.0.0-rc.1   # the rc section + the notes of 1.0.0

Exit code 1 if there is no such section.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def section(text: str, version: str) -> str | None:
    match = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)", text, re.M | re.S)
    return match.group(1).strip() if match else None


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    version = sys.argv[1].removeprefix("v")
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    own = section(text, version)
    if own is None:
        print(f"CHANGELOG.md has no section for {version}", file=sys.stderr)
        return 1
    parts = [own]
    base = version.split("-", 1)[0]
    if base != version:  # a pre-release: include the notes of the release it leads to
        notes = section(text, base)
        if notes:
            parts.append(f"## Notes for {base}\n\n{notes}")
    print("\n\n".join(parts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
