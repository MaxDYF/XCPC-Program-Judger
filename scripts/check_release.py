"""Fail a release if the Git index contains private runtime data or likely secrets."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DENIED_PARTS = {
    "data", "uploads", "reports", "references", "manifests",
    ".venv", "venv", "__pycache__", ".pytest_cache", ".git",
}
DENIED_SUFFIXES = {
    ".pdf", ".sqlite", ".sqlite3", ".db", ".pem", ".key",
    ".p12", ".pfx", ".pyc", ".log",
}
SECRET_PATTERNS = (
    re.compile(rb"sk-(?:cpa-)?[A-Za-z0-9_-]{20,}"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"(?i)(?:OPENAI_API_KEY|GITHUB_TOKEN|GH_TOKEN)\s*[:=]\s*['\"]?(?!replace-|your-|example|\$\{|os\.getenv)[A-Za-z0-9_-]{20,}"),
)


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT)


def main() -> int:
    try:
        entries = git("ls-files", "--stage", "-z").split(b"\0")
    except subprocess.CalledProcessError:
        print("Run this check in a Git repository after staging intended release files.", file=sys.stderr)
        return 2

    errors: list[str] = []
    for entry in filter(None, entries):
        meta, raw_path = entry.split(b"\t", 1)
        path = raw_path.decode("utf-8", "replace")
        parts = Path(path).parts
        lowered = path.lower()
        if any(part in DENIED_PARTS for part in parts):
            errors.append(f"{path}: private directory")
        if Path(lowered).suffix in DENIED_SUFFIXES or re.search(r"\.(?:sqlite3?|db)-(?:wal|shm|journal)$", lowered):
            errors.append(f"{path}: private file type")
        if Path(path).name.startswith(".env") and path != ".env.example":
            errors.append(f"{path}: environment file")
        if path.endswith((".json", ".jsonl")) and not path.startswith("validation/"):
            errors.append(f"{path}: unreviewed JSON")
        if meta.split()[0] == b"120000":
            errors.append(f"{path}: symbolic link requires manual review")
        blob = git("show", f":{path}")
        if b"\0" in blob:
            errors.append(f"{path}: binary content requires manual review")
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(blob):
                errors.append(f"{path}: likely secret pattern")
        home_prefix = b"/" + b"home/"
        private_host = b"judge." + b"maxrealm.top"
        private_ip = b"10.4." + b"165.11"
        if re.search(re.escape(home_prefix) + rb"[^/\s]+/", blob) or private_host in blob or private_ip in blob:
            errors.append(f"{path}: machine-specific path, address, or hostname")

    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"Release index check passed: {len(entries) - 1} tracked files; no private paths or secret patterns detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
