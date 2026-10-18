"""Persistent, public evaluation reports. Uploaded PDF and source bytes are never stored."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any


REPORT_FIELDS = (
    "history_id", "score", "score_scale", "relative_percentile", "target_rank",
    "total_problems", "reference_count", "excluded_self_references",
    "statement_clarity", "analysis", "ranking", "judge_reason", "calibration",
    "limitations",
)


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

    def save(self, history_id: str, report: dict[str, Any], created_at: float | None = None) -> None:
        public_report = {key: report[key] for key in REPORT_FIELDS if key in report}
        public_report["history_id"] = history_id
        title = str((public_report.get("analysis") or {}).get("summary") or "未命名题目").strip()[:160]
        clarity = public_report.get("statement_clarity") or {}
        body = json.dumps(public_report, ensure_ascii=False, separators=(",", ":"))
        with self._connect() as connection:
            connection.execute("""INSERT INTO evaluations
                (id, created_at, title, score, clarity_score, reference_count, report_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (history_id, created_at or time.time(), title, public_report.get("score"),
                 clarity.get("score"), public_report.get("reference_count"), body))

    def list(self, limit: int = 30, offset: int = 0) -> dict[str, Any]:
        with self._connect() as connection:
            total = connection.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0]
            rows = connection.execute("""SELECT id, created_at, title, score, clarity_score, reference_count
                FROM evaluations ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?""",
                (limit, offset)).fetchall()
        return {"total": total, "items": [dict(zip(
            ("id", "created_at", "title", "score", "clarity_score", "reference_count"), row))
            for row in rows]}

    def get(self, history_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT created_at, report_json FROM evaluations WHERE id = ?",
                                     (history_id,)).fetchone()
        return {"created_at": row[0], "report": json.loads(row[1])} if row else None
