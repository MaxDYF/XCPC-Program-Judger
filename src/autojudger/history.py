"""Persistent public reports and their explicitly downloadable uploaded files."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import stat
import time
from pathlib import Path
from typing import Any


REPORT_FIELDS = (
    "history_id", "score", "score_scale", "relative_percentile", "target_rank",
    "total_problems", "reference_count", "excluded_self_references",
    "statement_clarity", "analysis", "ranking", "judge_reason", "difficulty_assessment", "calibration",
    "limitations",
)
UPLOAD_FILES = {"pdf": "statement.pdf", "std": "standard-solution.txt"}
HISTORY_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{32}\Z")


class HistoryStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.getenv("AUTOJUDGER_HISTORY_DB") or
                         Path(__file__).resolve().parents[2] / "data" / "history.sqlite3")

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("""CREATE TABLE IF NOT EXISTS evaluations (
            id TEXT PRIMARY KEY,
            created_at REAL NOT NULL,
            title TEXT NOT NULL,
            score INTEGER,
            clarity_score REAL,
            reference_count INTEGER,
            report_json TEXT NOT NULL
        )""")
        connection.execute("CREATE INDEX IF NOT EXISTS evaluations_created_at ON evaluations(created_at DESC)")
        os.chmod(self.path, 0o600)
        return connection

    def _upload_dir(self, history_id: str) -> Path:
        if not HISTORY_ID_PATTERN.fullmatch(history_id):
            raise ValueError("Invalid history ID")
        return self.path.parent / "submissions" / history_id

    def _write_uploads(self, history_id: str, pdf: bytes, std: bytes) -> Path:
        directory = self._upload_dir(history_id)
        directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory.parent, 0o700)
        directory.mkdir(mode=0o700)
        try:
            for kind, content in (("pdf", pdf), ("std", std)):
                path = directory / UPLOAD_FILES[kind]
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "wb") as output:
                    output.write(content)
        except Exception:
            shutil.rmtree(directory)
            raise
        return directory

    def save(self, history_id: str, report: dict[str, Any], created_at: float | None = None,
             *, pdf: bytes | None = None, std: bytes | None = None) -> None:
        if (pdf is None) != (std is None):
            raise ValueError("Both uploaded files are required")
        public_report = {key: report[key] for key in REPORT_FIELDS if key in report}
        public_report["history_id"] = history_id
        title = str((public_report.get("analysis") or {}).get("summary") or "未命名题目").strip()[:160]
        clarity = public_report.get("statement_clarity") or {}
        body = json.dumps(public_report, ensure_ascii=False, separators=(",", ":"))
        directory = None
        try:
            if pdf is not None and std is not None:
                directory = self._write_uploads(history_id, pdf, std)
            with self._connect() as connection:
                connection.execute("""INSERT INTO evaluations
                    (id, created_at, title, score, clarity_score, reference_count, report_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (history_id, created_at or time.time(), title, public_report.get("score"),
                     clarity.get("score"), public_report.get("reference_count"), body))
        except Exception:
            if directory is not None:
                shutil.rmtree(directory)
            raise

    def list(self, limit: int = 30, offset: int = 0) -> dict[str, Any]:
        with self._connect() as connection:
            total = connection.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0]
            rows = connection.execute("""SELECT id, created_at, title, score, clarity_score, reference_count, report_json
                FROM evaluations ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?""",
                (limit, offset)).fetchall()
        items = []
        for row in rows:
            item = dict(zip(("id", "created_at", "title", "score", "clarity_score", "reference_count"), row[:6]))
            clarity = (json.loads(row[6]).get("statement_clarity") or {})
            item["clarity_score"] = clarity.get("weighted_score", item["clarity_score"])
            items.append(item)
        return {"total": total, "items": items}

    def get(self, history_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT created_at, report_json FROM evaluations WHERE id = ?",
                                     (history_id,)).fetchone()
        if not row:
            return None
        directory = self._upload_dir(history_id)
        uploads = {kind: (directory / filename).is_file() for kind, filename in UPLOAD_FILES.items()}
        return {"created_at": row[0], "report": json.loads(row[1]), "uploads": uploads}

    def open_upload(self, history_id: str, kind: str):
        if kind not in UPLOAD_FILES:
            raise FileNotFoundError("Unknown upload type")
        directory = self._upload_dir(history_id)
        with self._connect() as connection:
            exists = connection.execute("SELECT 1 FROM evaluations WHERE id = ?", (history_id,)).fetchone()
        if not exists:
            raise FileNotFoundError("History record not found")
        fd = os.open(directory / UPLOAD_FILES[kind], os.O_RDONLY | os.O_NOFOLLOW)
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode):
            os.close(fd)
            raise FileNotFoundError("Upload is not a regular file")
        return os.fdopen(fd, "rb"), file_stat.st_size
