import io
import threading
import time

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from autojudger import api, security


@pytest.fixture(autouse=True)
def isolated_history_db(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOJUDGER_HISTORY_DB", str(tmp_path / "history.sqlite3"))


def _pdf(pages: int = 1) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


def test_frontend_and_background_job(monkeypatch):
    monkeypatch.setattr(security, "_submission_limiter", security.SubmissionLimiter())
    pdf = _pdf()
    class FakeService:
        def evaluate(self, pdf, std, progress):
            assert pdf == expected_pdf
            assert std == b"int main(){}"
            progress("collecting", 2, 5)
            return {"score": 50}

    expected_pdf = pdf
    def fake_service(count):
        assert count == 5
        return FakeService()

    monkeypatch.setattr(api, "_service", fake_service)
    client = TestClient(api.app, base_url="http://localhost")
    assert client.get("/").status_code == 200
    assert client.get("/static/styles.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    response = client.post("/jobs", headers={"X-Autojudger-Request": "1"}, data={"reference_count": "5"}, files={
        "statement_pdf": ("problem.pdf", pdf, "application/pdf"),
        "std_file": ("std.cpp", b"int main(){}", "text/plain")})
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    for _ in range(50):
        status = client.get(f"/jobs/{job_id}").json()
        if status["status"] == "completed":
            break
        time.sleep(0.01)
    assert status["status"] == "completed"
    assert status["result"]["score"] == 50
    assert client.get("/api/history").json()["total"] == 1
    assert client.get(f"/api/history/{job_id}").json()["report"]["score"] == 50


def test_running_job_is_visible_in_public_history_without_uploads(monkeypatch):
    monkeypatch.setattr(security, "_submission_limiter", security.SubmissionLimiter())
    started = threading.Event()
    release = threading.Event()

    class FakeService:
        def evaluate(self, pdf, std, progress):
            progress("collecting", 2, 5)
            started.set()
            assert release.wait(5)
            return {"score": 1200, "analysis": {"summary": "测试题目"}}

    monkeypatch.setattr(api, "_service", lambda count: FakeService())
    client = TestClient(api.app, base_url="http://localhost")
    response = client.post("/jobs", headers={"X-XCPC-ProblemJudger-Request": "1"},
                           data={"reference_count": "5"}, files={
                               "statement_pdf": ("private.pdf", _pdf(), "application/pdf"),
                               "std_file": ("secret.cpp", b"PRIVATE_SOURCE", "text/plain")})
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    try:
        assert started.wait(2)
        listing = client.get("/api/jobs").json()
        item = next(item for item in listing["items"] if item["id"] == job_id)
        assert item["status"] == "running"
        assert item["phase"] == "collecting"
        assert item["reference_count"] == 5
        assert "PRIVATE_SOURCE" not in str(listing)
        assert "private.pdf" not in str(listing)
    finally:
        release.set()
    for _ in range(50):
        if client.get(f"/jobs/{job_id}").json()["status"] == "completed":
            break
        time.sleep(0.01)
    assert all(item["id"] != job_id for item in client.get("/api/jobs").json()["items"])
    entry = client.get(f"/api/history/{job_id}").json()
    assert entry["report"]["score"] == 1200
    assert entry["uploads"] == {"pdf": True, "std": True}
    assert client.get(f"/api/history/{job_id}/files/std").content == b"PRIVATE_SOURCE"


def test_history_page_downloads_original_uploads_without_putting_them_in_database(monkeypatch, tmp_path):
    monkeypatch.setattr(security, "_submission_limiter", security.SubmissionLimiter())

    class FakeService:
        def evaluate(self, pdf, std):
            assert std == b"SECRET_STD_MARKER"
            return {"score": 1600, "reference_count": 5,
                    "analysis": {"summary": "图上最短路", "algorithm": "BFS", "complexity": "O(n+m)",
                                 "inferred_tags": ["graphs"]},
                    "statement_clarity": {"score": 4, "weighted_score": 3.6, "max_score": 5, "verdict": "accept",
                                          "neutral_statement": "输入一个图，输出最短距离。"},
                    "ranking": []}

    monkeypatch.setattr(api, "_service", lambda count: FakeService())
    client = TestClient(api.app, base_url="http://localhost")
    pdf = _pdf()
    response = client.post("/evaluate", headers={"X-XCPC-ProblemJudger-Request": "1"}, files={
        "statement_pdf": ("problem.pdf", pdf, "application/pdf"),
        "std_file": ("std.cpp", b"SECRET_STD_MARKER", "text/plain")})
    assert response.status_code == 200
    history_id = response.json()["history_id"]
    assert client.get("/history").status_code == 200
    assert client.get(f"/history/{history_id}").status_code == 200
    assert client.get("/static/history.js").status_code == 200
    listing = client.get("/api/history").json()
    assert listing["total"] == 1
    assert listing["items"][0]["title"] == "图上最短路"
    assert listing["items"][0]["clarity_score"] == 3.6
    detail = client.get(f"/api/history/{history_id}").json()
    assert detail["report"]["score"] == 1600
    assert detail["uploads"] == {"pdf": True, "std": True}
    pdf_download = client.get(f"/api/history/{history_id}/files/pdf")
    std_download = client.get(f"/api/history/{history_id}/files/std")
    assert pdf_download.content == pdf
    assert std_download.content == b"SECRET_STD_MARKER"
    assert pdf_download.headers["content-disposition"] == 'attachment; filename="statement.pdf"'
    assert std_download.headers["content-disposition"] == 'attachment; filename="standard-solution.txt"'
    assert client.get(f"/api/history/{history_id}/files/other").status_code == 404
    assert client.get("/api/history/unknown/files/pdf").status_code == 404
    assert client.get("/api/history/unknown").status_code == 404
    assert b"SECRET_STD_MARKER" not in (tmp_path / "history.sqlite3").read_bytes()
    stored = tmp_path / "submissions" / history_id / "standard-solution.txt"
    assert stored.stat().st_mode & 0o777 == 0o600
    stored.unlink()
    stored.symlink_to(tmp_path / "history.sqlite3")
    assert client.get(f"/api/history/{history_id}/files/std").status_code == 404


def test_older_history_record_without_saved_uploads_has_no_download_links():
    history_id = "a" * 32
    api.HistoryStore().save(history_id, {"score": 1200, "analysis": {"summary": "早期记录"}})
    client = TestClient(api.app, base_url="http://localhost")
    entry = client.get(f"/api/history/{history_id}").json()
    assert entry["uploads"] == {"pdf": False, "std": False}
    assert client.get(f"/api/history/{history_id}/files/pdf").status_code == 404


def test_public_page_and_host_isolation(monkeypatch):
    client = TestClient(api.app, base_url="http://localhost")
    assert client.get("/").status_code == 200
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/static/../../README.md").status_code == 404
    assert client.get("/", headers={"Host": "attacker.example"}).status_code == 400
    monkeypatch.setenv("AUTOJUDGER_PUBLIC_HOST", "judge.example")
    assert client.get("/", headers={"Host": "judge.example"}).status_code == 200


def test_upload_guard_rejects_cross_site_and_oversized_requests(monkeypatch):
    monkeypatch.setattr(security, "_submission_limiter", security.SubmissionLimiter())
    client = TestClient(api.app, base_url="http://localhost")
    assert client.post("/jobs", content=b"x").status_code == 403
    assert client.post("/jobs", headers={"X-Autojudger-Request": "1", "Origin": "https://attacker.example"}, content=b"x").status_code == 403
    assert client.post("/jobs", headers={"X-Autojudger-Request": "1", "Content-Length": str(23 * 1024 * 1024)}, content=b"x").status_code == 413
    response = client.post("/jobs", headers={"X-Autojudger-Request": "1"}, content=b"x" * (22 * 1024 * 1024 + 1))
    assert response.status_code == 413
    assert client.get("/").headers["x-content-type-options"] == "nosniff"


def test_pdf_submission_limit_is_shared_by_both_endpoints_and_keyed_by_client_ip(monkeypatch):
    monkeypatch.setattr(security, "_submission_limiter", security.SubmissionLimiter())
    monkeypatch.setenv("AUTOJUDGER_PUBLIC_HOST", "judge.example")

    class FakeService:
        def evaluate(self, pdf, std, progress=None):
            return {"score": 800}

    monkeypatch.setattr(api, "_service", lambda count: FakeService())
    client = TestClient(api.app, base_url="https://judge.example", client=("127.0.0.1", 50000))
    files = {"statement_pdf": ("problem.pdf", _pdf(), "application/pdf"),
             "std_file": ("std.cpp", b"int main(){}", "text/plain")}
    headers = {"X-Autojudger-Request": "1", "CF-Connecting-IP": "203.0.113.10"}
    for index in range(5):
        endpoint = "/evaluate" if index == 0 else "/jobs"
        assert client.post(endpoint, headers=headers, files=files).status_code in (200, 202)
    blocked = client.post("/jobs", headers=headers, files=files)
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0
    assert "10 分钟" in blocked.json()["detail"]
    other_ip = {**headers, "CF-Connecting-IP": "203.0.113.11"}
    assert client.post("/jobs", headers=other_ip, files=files).status_code == 202


def test_pdf_over_ten_pages_is_rejected_before_job_creation(monkeypatch):
    monkeypatch.setattr(security, "_submission_limiter", security.SubmissionLimiter())
    client = TestClient(api.app, base_url="http://localhost")
    response = client.post("/jobs", headers={"X-Autojudger-Request": "1"}, files={
        "statement_pdf": ("long.pdf", _pdf(11), "application/pdf"),
        "std_file": ("std.cpp", b"int main(){}", "text/plain")})
    assert response.status_code == 422
    assert "10 pages" in response.json()["detail"]


def test_client_ip_header_is_ignored_outside_trusted_tunnel():
    headers = {b"cf-connecting-ip": "203.0.113.15", b"x-forwarded-for": "203.0.113.16"}
    assert security._client_ip({"client": ("198.51.100.8", 1234)}, headers,
                               {"judge.example"}, "judge.example") == "198.51.100.8"
    assert security._client_ip({"client": ("127.0.0.1", 1234)}, headers,
                               {"judge.example"}, "localhost") == "127.0.0.1"


def test_submission_window_reopens_after_ten_minutes():
    now = [1000.0]
    limiter = security.SubmissionLimiter(clock=lambda: now[0])
    assert [limiter.take("203.0.113.1") for _ in range(5)] == [0] * 5
    assert limiter.take("203.0.113.1") == 600
    now[0] += 599
    assert limiter.take("203.0.113.1") == 1
    now[0] += 1
    assert limiter.take("203.0.113.1") == 0


def test_model_routing_keeps_scoring_on_sol_and_formatting_on_luna(monkeypatch):
    for key in ("JUDGE_MODEL", "CLARITY_MODEL", "ANALYST_MODEL", "INTERPRETER_MODEL",
                "EXTRACTOR_MODEL", "FORMAT_MODEL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "gpt-6-luna")

    class FakeChatAgent:
        def __init__(self, model=None, timeout_seconds=180):
            self.model = model or "gpt-6-luna"
            self.timeout_seconds = timeout_seconds

    monkeypatch.setattr(api, "ChatAgent", FakeChatAgent)
    service = api._service(10)
    assert service.analyst.llm.model == "gpt-6-luna"
    assert service.interpreter.llm.model == "gpt-6-luna"
    assert service.provider.extractor.llm.model == "gpt-6-luna"
    assert service.judge.llm.model == "gpt-6-sol"
    assert service.judge.llm.timeout_seconds == 360
    assert service.clarity_judge.llm.model == "gpt-6-sol"
    assert service.judge.localizer.model == "gpt-6-luna"
    assert service.clarity_judge.localizer.model == "gpt-6-luna"
