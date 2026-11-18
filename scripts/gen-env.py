#!/usr/bin/env python3
"""Create .env from .env.example, replacing every __GENERATE__ with a random secret.

Usage:  python scripts/gen-env.py [--force]
"""
import pathlib
import secrets
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / ".env.example"
DST = ROOT / ".env"


def main() -> int:
    if DST.exists() and "--force" not in sys.argv:
        print(f"{DST} already exists. Use --force to overwrite.")
        return 1
    lines = []
    for line in SRC.read_text(encoding="utf-8").splitlines():
        while "__GENERATE__" in line:
            line = line.replace("__GENERATE__", secrets.token_urlsafe(24), 1)
        lines.append(line)
    DST.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Written {DST}. Bootstrap admin password: see CDS_BOOTSTRAP_ADMIN_PASSWORD in it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
