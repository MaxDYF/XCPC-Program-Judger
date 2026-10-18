from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .agents import AnalystAgent, BlindJudgeAgent, ClarityJudgeAgent, EditorialExtractionAgent, InterpretationAgent
from .codeforces import CodeforcesProvider, ManifestProvider
from .llm import ChatAgent
from .models import JudgingError
from .service import JudgingService


def main() -> int:
    parser = argparse.ArgumentParser(description="Rank an ICPC problem against Codeforces references")
    parser.add_argument("statement_pdf", type=Path)
    parser.add_argument("std_file", type=Path)
    parser.add_argument("--references", type=int, default=12, choices=range(5, 21), metavar="5..20")
    parser.add_argument("--manifest", type=Path, help="Offline Codeforces reference corpus JSON")
    parser.add_argument("--output", type=Path, help="Write report JSON to this file")
    args = parser.parse_args()
    try:
        format_llm = ChatAgent(model=os.getenv("FORMAT_MODEL") or "gpt-6-luna")
        analyst_llm = ChatAgent(model=os.getenv("ANALYST_MODEL"))
        judge_llm = ChatAgent(model=os.getenv("JUDGE_MODEL") or "gpt-6-sol", timeout_seconds=360)
        provider = (ManifestProvider(args.manifest) if args.manifest else CodeforcesProvider(
            extractor=EditorialExtractionAgent(ChatAgent(model=os.getenv("EXTRACTOR_MODEL")))))
        service = JudgingService(AnalystAgent(analyst_llm), BlindJudgeAgent(judge_llm, format_llm), provider, args.references,
            interpreter=InterpretationAgent(ChatAgent(model=os.getenv("INTERPRETER_MODEL"))),
            clarity_judge=ClarityJudgeAgent(ChatAgent(model=os.getenv("CLARITY_MODEL") or "gpt-6-sol"), format_llm))
        report = service.evaluate(args.statement_pdf.read_bytes(), args.std_file.read_bytes())
    except (JudgingError, OSError) as exc:
        parser.exit(1, f"Error: {exc}\n")
    result = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(result + "\n", encoding="utf-8")
    else:
        print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
