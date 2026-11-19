#!/usr/bin/env python3
"""Create .env from .env.example, replacing every __GENERATE__ with a random secret.

Usage:
  python scripts/gen-env.py            # new installation: write .env
  python scripts/gen-env.py --force    # overwrite an existing .env (new secrets!)
  python scripts/gen-env.py --update   # existing installation: append variables that appeared in
                                       # .env.example since (e.g. DB_PASSWORD_CREDIT); existing
                                       # values are never changed
"""
import pathlib
import secrets
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / ".env.example"
DST = ROOT / ".env"


def fill(line: str) -> str:
    while "__GENERATE__" in line:
        line = line.replace("__GENERATE__", secrets.token_urlsafe(24), 1)
    return line


def key_of(line: str) -> str | None:
    text = line.strip()
    if not text or text.startswith("#") or "=" not in text:
        return None
    return text.split("=", 1)[0].strip()


def update() -> int:
    if not DST.exists():
        print(f"{DST} does not exist: run without --update first.")
        return 1
    text = DST.read_text(encoding="utf-8")
    existing = {key_of(line) for line in text.splitlines()}
    added = [fill(line) for line in SRC.read_text(encoding="utf-8").splitlines() if key_of(line) and key_of(line) not in existing]
    if not added:
        print(f"{DST} is up to date.")
        return 0
    if not text.endswith("\n"):
        text += "\n"
    text += "\n# Added by scripts/gen-env.py --update\n" + "\n".join(added) + "\n"
    DST.write_text(text, encoding="utf-8", newline="\n")  # LF even on Windows
    print("Added to .env: " + ", ".join(key_of(line) for line in added))
    return 0


def main() -> int:
    if "--update" in sys.argv:
        return update()
    if DST.exists() and "--force" not in sys.argv:
        print(f"{DST} already exists. Use --update to add new variables or --force to overwrite.")
        return 1
    lines = [fill(line) for line in SRC.read_text(encoding="utf-8").splitlines()]
    DST.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"Written {DST}. Bootstrap admin password: see CDS_BOOTSTRAP_ADMIN_PASSWORD in it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
