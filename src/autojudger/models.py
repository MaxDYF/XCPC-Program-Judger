from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Analysis:
    summary: str
    algorithm: str
    complexity: str
    tags: tuple[str, ...]


@dataclass(frozen=True)
class Reference:
    contest_id: int
    index: str
    title: str
    rating: int
    tags: tuple[str, ...]
    statement: str
    source: str
    submission_id: int | None = None
    editorial_url: str | None = None
    source_url: str | None = None
    source_method: str = "unknown"

    @property
    def url(self) -> str:
        return f"https://codeforces.com/problemset/problem/{self.contest_id}/{self.index}"


@dataclass(frozen=True)
class BlindProblem:
    anonymous_id: str
    statement: str
    source: str


class JudgingError(RuntimeError):
    """A required input, provider, or agent operation failed."""


class SourceAccessDenied(JudgingError):
    """Codeforces submission source cannot be read in this environment."""
