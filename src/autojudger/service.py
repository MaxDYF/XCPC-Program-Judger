from __future__ import annotations

import io
import math
import os
import random
import re
import secrets
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Any, Callable, Protocol

from pypdf import PdfReader

from .agents import AnalystAgent, BlindJudgeAgent, ClarityJudgeAgent, InterpretationAgent
from .calibration import calibrate
from .codeforces import select_candidates
from .models import BlindProblem, JudgingError, Reference, SourceAccessDenied


class ReferenceProvider(Protocol):
    def catalog(self) -> list[dict[str, Any]]: ...
    def hydrate(self, problem: dict[str, Any]) -> Reference: ...


def validate_pdf(data: bytes) -> PdfReader:
    if not data.startswith(b"%PDF-"):
        raise JudgingError("Uploaded statement is not a PDF")
    if len(data) > 20 * 1024 * 1024:
        raise JudgingError("PDF exceeds 20 MB")
    try:
        reader = PdfReader(io.BytesIO(data))
        if len(reader.pages) > 10:
            raise JudgingError("PDF exceeds 10 pages")
    except JudgingError:
        raise
    except Exception as exc:
        raise JudgingError("Cannot read PDF") from exc
    return reader


def extract_pdf(data: bytes) -> str:
    reader = validate_pdf(data)
    try:
        text = "\n\n".join(page.extract_text() or "" for page in reader.pages).strip()
    except JudgingError:
        raise
    except Exception as exc:
        raise JudgingError(f"Cannot read PDF: {exc}") from exc
    if len(text) < 100:
        raise JudgingError("PDF has little extractable text; provide a text-based PDF")
    return text


def decode_source(data: bytes) -> str:
    if len(data) > 1024 * 1024:
        raise JudgingError("Standard solution exceeds 1 MB")
    try:
        source = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise JudgingError("Standard solution must be UTF-8") from exc
    if len(source.strip()) < 20:
        raise JudgingError("Standard solution is empty")
    return source


def balanced_references(pool: list[Reference], count: int) -> list[Reference] | None:
    """Pick a wide, reasonably dense rating ladder from verified references."""
    if len(pool) < count:
        return None
    low, high = min(r.rating for r in pool), max(r.rating for r in pool)
    selected: list[Reference] = []
    remaining = pool.copy()
    for i in range(count):
        target = low + (high - low) * i / (count - 1)
        choice = min(remaining, key=lambda r: (abs(r.rating - target), r.contest_id, r.index))
        selected.append(choice)
        remaining.remove(choice)
    ratings = sorted(r.rating for r in selected)
    allowed_gap = max(600, math.ceil((ratings[-1] - ratings[0]) / (count - 1) * 1.25))
    if (len(set(ratings)) < math.ceil(count * 0.7) or ratings[-1] - ratings[0] < 800
            or max(b - a for a, b in zip(ratings, ratings[1:])) > allowed_gap):
        return None
    return selected


def same_problem(statement: str, source: str, reference: Reference) -> bool:
    """Detect the uploaded problem in a CF candidate despite PDF line breaks."""
    compact_source = re.sub(r"\s+", "", source)
    if len(compact_source) >= 100 and compact_source == re.sub(r"\s+", "", reference.source):
        return True
    left = re.findall(r"[a-z0-9]+", statement.casefold())
    right = re.findall(r"[a-z0-9]+", reference.statement.casefold())
    if min(len(left), len(right)) < 50:
        return False
    width = 4
    a = {tuple(left[i:i + width]) for i in range(len(left) - width + 1)}
    b = {tuple(right[i:i + width]) for i in range(len(right) - width + 1)}
    overlap = len(a & b)
    return overlap / min(len(a), len(b)) >= 0.8 and overlap / len(a | b) >= 0.6


class JudgingService:
    def __init__(self, analyst: AnalystAgent, judge: BlindJudgeAgent,
                 provider: ReferenceProvider, reference_count: int = 12,
                 ranking_rounds: int | None = None,
                 interpreter: InterpretationAgent | None = None,
                 clarity_judge: ClarityJudgeAgent | None = None):
        if not 5 <= reference_count <= 20:
            raise ValueError("reference_count must be 5-20")
        self.analyst = analyst
        self.judge = judge
        self.interpreter = interpreter or InterpretationAgent(analyst.llm)
        self.clarity_judge = clarity_judge or ClarityJudgeAgent(analyst.llm)
        self.provider = provider
        self.reference_count = reference_count
        self.ranking_rounds = ranking_rounds if ranking_rounds is not None else int(os.getenv("BLIND_RANK_ROUNDS", "3"))
        if not 1 <= self.ranking_rounds <= 5:
            raise ValueError("ranking_rounds must be 1-5")

    def evaluate(self, pdf: bytes, std: bytes,
                 progress: Callable[[str, int, int], None] | None = None) -> dict[str, Any]:
        def emit(phase: str, completed: int = 0, total: int = 0) -> None:
            if progress:
                progress(phase, completed, total)

        emit("analyzing")
        statement = extract_pdf(pdf)
        source = decode_source(std)
        analysis = self.analyst.analyze(statement, source)
        emit("clarity")
        interpretation = self.interpreter.interpret(statement)
        clarity = self.clarity_judge.score(statement, interpretation)
        emit("searching")
        catalog = self.provider.catalog()
        candidates = select_candidates(catalog, analysis.tags, self.reference_count)
        references: list[Reference] = []
        chosen_references: list[Reference] | None = None
        failures: list[str] = []
        excluded_self: list[str] = []
        seen: set[tuple[int, str]] = set()
        emit("collecting", 0, self.reference_count)
        candidate_iter = iter(candidates)
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="reference-fetch") as pool:
            pending = {}

            def fill_slots() -> None:
                while len(pending) < 4:
                    candidate = next(candidate_iter, None)
                    if candidate is None:
                        break
                    pending[pool.submit(self.provider.hydrate, candidate)] = candidate

            fill_slots()
            while pending and chosen_references is None:
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    pending.pop(future)
                    try:
                        ref = future.result()
                    except (SourceAccessDenied, JudgingError) as exc:
                        failures.append(str(exc))
                        continue
                    if same_problem(statement, source, ref):
                        excluded_self.append(f"{ref.contest_id}{ref.index}")
                        continue
                    key = (ref.contest_id, ref.index)
                    if key not in seen:
                        references.append(ref)
                        seen.add(key)
                        emit("collecting", min(len(references), self.reference_count), self.reference_count)
                        chosen_references = balanced_references(references, self.reference_count)
                        if chosen_references is not None:
                            break
                if chosen_references is None:
                    fill_slots()
            for future in pending:
                future.cancel()
        if chosen_references is None:
            detail = failures[-1] if failures else "no usable references"
            raise JudgingError(f"Only {len(references)} verified references, but no balanced {self.reference_count}-problem rating gradient. Last failure: {detail}")
        references = chosen_references

        # The judge gets a fresh, anonymous data structure. Structured IDs,
        # titles, ratings, tags, URLs, submission IDs, and analyst output stay out.
        target_id = f"p_{secrets.token_hex(6)}"
        reference_ids = {f"p_{secrets.token_hex(6)}": ref for ref in references}
        blind = [BlindProblem(target_id, statement, source)]
        blind.extend(BlindProblem(anonymous_id, ref.statement, ref.source)
                     for anonymous_id, ref in reference_ids.items())
        orders: list[list[str]] = []
        reasons: list[str] = []
        target_assessments: list[dict[str, str]] = []
        emit("ranking", 0, self.ranking_rounds)
        for round_index in range(self.ranking_rounds):
            random.SystemRandom().shuffle(blind)
            order, reason, assessments = self.judge.rank(blind)
            orders.append(order)
            reasons.append(reason)
            target_assessments.append(assessments[target_id])
            emit("ranking", round_index + 1, self.ranking_rounds)
        calibrated = calibrate(target_id, reference_ids, orders)
        representative_round = sorted(range(len(orders)), key=lambda i: orders[i].index(target_id))[len(orders) // 2]
        anchors = sorted(reference_ids, key=lambda ident: (reference_ids[ident].rating,
                                                           reference_ids[ident].contest_id,
                                                           reference_ids[ident].index))
        anchors.insert(calibrated.rank - 1, target_id)
        ranking = []
        for position, anonymous_id in enumerate(anchors, 1):
            if anonymous_id == target_id:
                ranking.append({"rank": position, "target": True})
            else:
                ref = reference_ids[anonymous_id]
                ranking.append({"rank": position, "target": False,
                                "codeforces_id": f"{ref.contest_id}{ref.index}",
                                "title": ref.title, "rating": ref.rating,
                                "tags": list(ref.tags), "url": ref.url,
                                "editorial_url": ref.editorial_url,
                                "source_url": ref.source_url,
                                "source_method": ref.source_method,
                                "accepted_submission_id": ref.submission_id})
        return {"score": calibrated.score, "score_scale": "估计的 Codeforces 题目难度分",
                "relative_percentile": calibrated.relative_percentile,
                "target_rank": calibrated.rank, "total_problems": len(anchors),
                "reference_count": len(references),
                "excluded_self_references": excluded_self,
                "statement_clarity": clarity,
                "analysis": {"summary": analysis.summary, "algorithm": analysis.algorithm,
                             "complexity": analysis.complexity, "inferred_tags": list(analysis.tags)},
                "ranking": ranking, "judge_reason": reasons[representative_round],
                "difficulty_assessment": target_assessments[representative_round],
                "calibration": {"method": "多轮匿名排序、成对比较的单调校准、最小冲突插入、相邻 Codeforces rating 插值",
                                "rounds": self.ranking_rounds,
                                "raw_target_ranks": list(calibrated.raw_ranks),
                                "calibrated_references_easier": calibrated.references_easier,
                                "equally_optimal_rank_interval": list(calibrated.rank_interval),
                                "estimated_rating_interval": list(calibrated.estimated_rating_interval),
                                "neighbor_ratings": list(calibrated.rating_neighbors),
                                "anchor_inversion_rate": calibrated.anchor_inversion_rate,
                                "stability": calibrated.stability},
                "limitations": "难度由启发式比较估计，不能证明 std 正确；参考代码在可获取时从官方题解提取。"}
