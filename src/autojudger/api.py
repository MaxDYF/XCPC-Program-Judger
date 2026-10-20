from __future__ import annotations

import os
import secrets
import threading
import time
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .agents import AnalystAgent, BlindJudgeAgent, ClarityJudgeAgent, EditorialExtractionAgent, InterpretationAgent
from .codeforces import CodeforcesProvider, ManifestProvider
from .llm import ChatAgent
from .models import JudgingError
from .service import JudgingService, validate_pdf
from .security import SecurityMiddleware
from .history import HistoryStore

app = FastAPI(title="XCPC-ProblemJudger", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SecurityMiddleware)
app.mount("/static", StaticFiles(directory=Path(__file__).with_name("static")), name="static")
_executor = ThreadPoolExecutor(max_workers=2)
_job_slots = threading.BoundedSemaphore(4)
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
_logger = logging.getLogger(__name__)


def _prune_jobs() -> None:
    """Call with _jobs_lock held; results expire after an hour."""
    cutoff = time.time() - 3600
    for job_id, job in list(_jobs.items()):
        if job.get("finished_at", float("inf")) < cutoff:
            del _jobs[job_id]
    finished = sorted(((job["finished_at"], job_id) for job_id, job in _jobs.items()
                       if "finished_at" in job))
    for _, job_id in finished[:max(0, len(_jobs) - 100)]:
        del _jobs[job_id]


def _service(reference_count: int) -> JudgingService:
    manifest = os.getenv("REFERENCE_MANIFEST")
    format_llm = ChatAgent(model=os.getenv("FORMAT_MODEL") or "gpt-6-luna")
    analyst_llm = ChatAgent(model=os.getenv("ANALYST_MODEL"))
    judge_llm = ChatAgent(model=os.getenv("JUDGE_MODEL") or "gpt-6-sol", timeout_seconds=360)
    interpretation_llm = ChatAgent(model=os.getenv("INTERPRETER_MODEL"))
    clarity_llm = ChatAgent(model=os.getenv("CLARITY_MODEL") or "gpt-6-sol")
    provider = (ManifestProvider(manifest) if manifest else CodeforcesProvider(
        extractor=EditorialExtractionAgent(ChatAgent(model=os.getenv("EXTRACTOR_MODEL")))))
    return JudgingService(AnalystAgent(analyst_llm), BlindJudgeAgent(judge_llm, format_llm), provider, reference_count,
                          interpreter=InterpretationAgent(interpretation_llm),
                          clarity_judge=ClarityJudgeAgent(clarity_llm, format_llm))


def _read_uploads(statement_pdf: UploadFile, std_file: UploadFile) -> tuple[bytes, bytes]:
    pdf = statement_pdf.file.read(20 * 1024 * 1024 + 1)
    std = std_file.file.read(1024 * 1024 + 1)
    if not pdf.startswith(b"%PDF-") or len(pdf) > 20 * 1024 * 1024:
        raise HTTPException(422, "Upload a PDF under 20 MB")
    try:
        validate_pdf(pdf)
    except JudgingError as exc:
        raise HTTPException(422, str(exc)) from exc
    if len(std) > 1024 * 1024 or not std.strip():
        raise HTTPException(422, "Upload a nonempty std file under 1 MB")
    return pdf, std


def _run_job(job_id: str, pdf: bytes, std: bytes, reference_count: int) -> None:
    def progress(phase: str, completed: int, total: int) -> None:
        with _jobs_lock:
            _jobs[job_id].update(status="running", phase=phase, completed=completed, total=total)

    try:
        result = _service(reference_count).evaluate(pdf, std, progress)
        result["history_id"] = job_id
        with _jobs_lock:
            created_at = _jobs[job_id]["created_at"]
        HistoryStore().save(job_id, result, created_at, pdf=pdf, std=std)
        with _jobs_lock:
            _jobs[job_id].update(status="completed", phase="completed", result=result, finished_at=time.time())
    except (JudgingError, OSError, ValueError):
        _logger.exception("Evaluation job %s failed", job_id)
        with _jobs_lock:
            _jobs[job_id].update(status="failed", phase="failed", error="评估失败，请检查输入或联系管理员。", finished_at=time.time())
    except Exception:
        _logger.exception("Evaluation job %s failed unexpectedly", job_id)
        with _jobs_lock:
            _jobs[job_id].update(status="failed", phase="failed", error="评估失败，请联系管理员。", finished_at=time.time())
    finally:
        _job_slots.release()


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return Path(__file__).with_name("index.html").read_text(encoding="utf-8")


@app.get("/history", response_class=HTMLResponse)
@app.get("/history/{history_id}", response_class=HTMLResponse)
def history_page(history_id: str | None = None) -> str:
    return Path(__file__).with_name("history.html").read_text(encoding="utf-8")


@app.get("/api/history")
def list_history(limit: int = Query(30, ge=1, le=50),
                 offset: int = Query(0, ge=0, le=1_000_000)) -> dict:
    return HistoryStore().list(limit, offset)


@app.get("/api/history/{history_id}")
def get_history(history_id: str) -> dict:
    if len(history_id) > 128:
        raise HTTPException(404, "History record not found")
    entry = HistoryStore().get(history_id)
    if entry is None:
        raise HTTPException(404, "History record not found")
    return entry


@app.get("/api/history/{history_id}/files/{kind}")
def download_upload(history_id: str, kind: str) -> StreamingResponse:
    try:
        source, size = HistoryStore().open_upload(history_id, kind)
    except (OSError, ValueError):
        raise HTTPException(404, "Uploaded file not found") from None

    def chunks():
        try:
            while chunk := source.read(64 * 1024):
                yield chunk
        finally:
            source.close()

    filename = "statement.pdf" if kind == "pdf" else "standard-solution.txt"
    return StreamingResponse(chunks(), media_type="application/octet-stream",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"',
                                      "Content-Length": str(size)})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/evaluate")
def evaluate(statement_pdf: UploadFile = File(...), std_file: UploadFile = File(...),
             reference_count: int = Form(12)) -> dict:
    if not 5 <= reference_count <= 20:
        raise HTTPException(422, "reference_count must be 5-20")
    if not _job_slots.acquire(blocking=False):
        raise HTTPException(429, "Evaluation capacity is full")
    try:
        pdf, std = _read_uploads(statement_pdf, std_file)
        result = _service(reference_count).evaluate(pdf, std)
        history_id = secrets.token_urlsafe(24)
        result["history_id"] = history_id
        HistoryStore().save(history_id, result, pdf=pdf, std=std)
        return result
    except JudgingError as exc:
        _logger.exception("Synchronous evaluation failed")
        raise HTTPException(422, "评估失败，请检查输入或联系管理员。") from exc
    finally:
        _job_slots.release()


@app.post("/jobs", status_code=202)
def create_job(statement_pdf: UploadFile = File(...), std_file: UploadFile = File(...),
               reference_count: int = Form(12)) -> dict[str, str]:
    if not 5 <= reference_count <= 20:
        raise HTTPException(422, "reference_count must be 5-20")
    if not _job_slots.acquire(blocking=False):
        raise HTTPException(429, "Evaluation capacity is full")
    job_id = secrets.token_urlsafe(24)
    try:
        pdf, std = _read_uploads(statement_pdf, std_file)
        with _jobs_lock:
            _prune_jobs()
            if len(_jobs) >= 100:
                raise HTTPException(429, "Job history capacity is full")
            _jobs[job_id] = {"created_at": time.time(), "status": "queued", "phase": "queued",
                             "completed": 0, "total": 0, "reference_count": reference_count}
        try:
            _executor.submit(_run_job, job_id, pdf, std, reference_count)
        except Exception:
            with _jobs_lock:
                del _jobs[job_id]
            raise
    except Exception:
        _job_slots.release()
        raise
    return {"job_id": job_id}


@app.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    with _jobs_lock:
        _prune_jobs()
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Job not found")
        return {key: value for key, value in job.items() if key not in ("created_at", "finished_at")}


@app.get("/api/jobs")
def list_jobs() -> dict:
    """Public, metadata-only snapshot of jobs that have not completed."""
    with _jobs_lock:
        _prune_jobs()
        items = [
            {"id": job_id, "created_at": job["created_at"], "status": job["status"],
             "phase": job["phase"], "completed": job["completed"], "total": job["total"],
             "reference_count": job["reference_count"]}
            for job_id, job in _jobs.items() if job["status"] in ("queued", "running")
        ]
    items.sort(key=lambda item: item["created_at"], reverse=True)
    return {"total": len(items), "items": items}
