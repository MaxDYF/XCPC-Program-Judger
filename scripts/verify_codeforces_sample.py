"""Capture a reproducible, metadata-only audit of ten live Codeforces references.

Run with OPENAI_API_KEY and OPENAI_BASE_URL set. The key is used only when a
nonstandard editorial needs the extraction agent; it is never written out.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from autojudger.agents import EditorialExtractionAgent
from autojudger.codeforces import CodeforcesProvider
from autojudger.llm import ChatAgent
from autojudger.models import JudgingError


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "validation" / "codeforces-sample.json"
TARGETS = [1000, 1200, 1400, 1600, 1800, 2000, 2200]
SEEDS = [(1234, "A"), (1990, "A"), (2032, "A")]


def check_source(source: str) -> str:
    """Check C++ or Python syntax without running contest programs."""
    if not CodeforcesProvider._complete_program(source):
        raise JudgingError("source is not a complete program")
    if "#include" not in source:
        if "def " in source or "import " in source or "input(" in source:
            try:
                ast.parse(source)
            except SyntaxError as exc:
                raise JudgingError(f"Python syntax check failed: {exc}") from exc
            return "Python ast.parse passed"
        return "complete-program heuristic (other language)"
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "solution.cpp"
        path.write_text(source, encoding="utf-8")
        try:
            result = subprocess.run(
                ["g++", "-std=gnu++20", "-fsyntax-only", str(path)],
                capture_output=True, text=True, timeout=12, check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            raise JudgingError(f"C++ syntax check unavailable: {exc}") from exc
        if result.returncode:
            raise JudgingError(f"C++ syntax check failed: {result.stderr[:180]}")
    return "g++ -std=gnu++20 -fsyntax-only passed"


def main() -> None:
    extractor = EditorialExtractionAgent(ChatAgent(model=os.getenv("EXTRACTOR_MODEL")))
    provider = CodeforcesProvider(extractor=extractor)
    catalog = provider.catalog()
    by_id = {(p.get("contestId"), p.get("index")): p for p in catalog}
    groups = [[by_id[key]] for key in SEEDS if key in by_id]
    for rating in TARGETS:
        group = [p for p in catalog if p.get("rating") == rating
                 and "math" in p.get("tags", [])
                 and "interactive" not in p.get("tags", [])
                 and 1200 <= p.get("contestId", 0) <= 2100]
        group.sort(key=lambda p: (abs(p["contestId"] - 1900), p["contestId"], p["index"]))
        groups.append(group[:12])

    verified = []
    failures = []
    used = set()
    for group in groups:
        for problem in group:
            key = (problem["contestId"], problem["index"])
            if key in used:
                continue
            used.add(key)
            code = f"{key[0]}{key[1]}"
            try:
                ref = provider.hydrate(problem)
                syntax_check = check_source(ref.source)
                if not ref.editorial_url or len(ref.statement) < 100:
                    raise JudgingError("missing statement or editorial provenance")
            except JudgingError as exc:
                failures.append({"id": code, "reason": str(exc)[:250]})
                print(f"SKIP {code}: {str(exc)[:140]}", flush=True)
                continue
            verified.append({
                "id": code, "title": ref.title, "rating": ref.rating,
                "tags": list(ref.tags), "problem_url": ref.url,
                "editorial_url": ref.editorial_url, "source_url": ref.source_url,
                "source_method": ref.source_method,
                "statement_chars": len(ref.statement), "source_chars": len(ref.source),
                "statement_sha256": hashlib.sha256(ref.statement.encode()).hexdigest(),
                "source_sha256": hashlib.sha256(ref.source.encode()).hexdigest(),
                "syntax_check": syntax_check,
            })
            print(f"OK {code} CF {ref.rating} {ref.source_method} {syntax_check}", flush=True)
            break

    report = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "verified_count": len(verified), "attempted_count": len(used),
        "criteria": "live CF statement + official editorial + complete code + C++ syntax check when applicable",
        "verified": verified, "failures": failures,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Report: {OUT}", flush=True)
    if len(verified) != 10:
        raise SystemExit("Could not verify ten complete references")


if __name__ == "__main__":
    main()
