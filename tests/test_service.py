import io
import json

import pytest
import httpx
from pypdf import PdfWriter

from autojudger.agents import AnalystAgent, BlindJudgeAgent, ClarityJudgeAgent, EditorialExtractionAgent, InterpretationAgent
from autojudger.calibration import _monotone_probabilities, calibrate
from autojudger.codeforces import CodeforcesProvider, select_candidates
from autojudger.models import BlindProblem, JudgingError, Reference, SourceAccessDenied
from autojudger.llm import ChatAgent
from autojudger.service import JudgingService, balanced_references, extract_pdf, same_problem


class FakeLLM:
    def __init__(self):
        self.calls = []

    def ask_json(self, system, payload):
        self.calls.append((system, payload))
        if "neutral interpretation agent" in system:
            return {"neutral_statement": "输入图的顶点与边，输出从起点能到达的顶点数量。",
                    "unresolved_ambiguities": []}
        if "statement clarity judge" in system:
            assert "standard_solution" not in payload
            return {"scores": {"objective_io": 4, "language": 5, "information_focus": 3,
                                "background_independence": 5},
                    "notes": {key: "题目要求可以理解。" for key in
                              ("objective_io", "language", "information_focus", "background_independence")},
                    "summary": "题意基本清楚，但信息可以更集中。", "issues": ["背景段落略长"]}
        if "select 1-4" in system:
            return {"summary": "图上可达性任务", "algorithm": "使用 BFS 搜索", "complexity": "时间复杂度 O(n+m)", "tags": ["graphs"]}
        ids = [p["id"] for p in payload["problems"]]
        # Put the target first; fake provider marks references with REF in code.
        target = next(p["id"] for p in payload["problems"] if "TARGET" in p["standard_solution"])
        return {"order": [target] + [x for x in ids if x != target], "reason": "目标题最容易。"}


class FakeProvider:
    def catalog(self):
        return [{"contestId": 100 + i, "index": "A", "name": f"Problem {i}",
                 "rating": 800 + i * 150, "tags": ["graphs"]} for i in range(12)]

    def hydrate(self, p):
        return Reference(p["contestId"], p["index"], p["name"], p["rating"],
                         tuple(p["tags"]), "A graph statement with input and output constraints. " * 3,
                         "// REF\nint main() { return 0; }", 12345)


def test_blind_boundary_and_scoring(monkeypatch):
    monkeypatch.setattr("autojudger.service.extract_pdf", lambda _: "A complete target statement. " * 10)
    llm = FakeLLM()
    service = JudgingService(AnalystAgent(llm), BlindJudgeAgent(llm), FakeProvider(), 10)
    result = service.evaluate(b"pdf", b"// TARGET\nint main() { return 0; }")
    assert result["score"] == 800
    assert result["statement_clarity"]["score"] == 5
    assert result["statement_clarity"]["weighted_score"] == 4.6
    assert result["statement_clarity"]["verdict"] == "strong accept"
    assert result["statement_clarity"]["passed"] is True
    assert result["relative_percentile"] == 0
    assert result["target_rank"] == 1
    assert len(result["ranking"]) == 11
    assert result["calibration"]["rounds"] == 3
    judge_payload = llm.calls[3][1]
    assert set(judge_payload) == {"problems"}
    assert all(set(p) == {"id", "statement", "standard_solution"} for p in judge_payload["problems"])
    assert "rating" not in json.dumps(judge_payload)
    assert "tags" not in json.dumps(judge_payload)
    assert "codeforces" not in json.dumps(judge_payload).lower()


def test_five_references_complete_pipeline(monkeypatch):
    monkeypatch.setattr("autojudger.service.extract_pdf", lambda _: "A complete target statement. " * 10)
    llm = FakeLLM()
    result = JudgingService(AnalystAgent(llm), BlindJudgeAgent(llm), FakeProvider(), 5).evaluate(
        b"pdf", b"// TARGET\nint main() { return 0; }")
    assert result["reference_count"] == 5
    assert len(result["ranking"]) == 6
    assert result["calibration"]["rounds"] == 3
    assert result["score"] >= 800


@pytest.mark.parametrize("count", [5, 20])
def test_reference_count_boundaries_are_accepted(count):
    catalog = [{"contestId": 100 + i, "index": "A", "rating": 800 + 90 * i,
                "tags": ["graphs"]} for i in range(20)]
    assert len(select_candidates(catalog, ("graphs",), count)) >= count
    refs = {f"r{i}": Reference(100 + i, "A", f"Problem {i}", 800 + 100 * i,
                              ("graphs",), "statement", "source") for i in range(count)}
    order = ["target", *refs]
    assert calibrate("target", refs, [order]).rank == 1


def test_missing_complete_references_never_scores(monkeypatch):
    monkeypatch.setattr("autojudger.service.extract_pdf", lambda _: "A complete target statement. " * 10)

    class BrokenProvider(FakeProvider):
        def hydrate(self, p):
            raise JudgingError("source unavailable")

    llm = FakeLLM()
    service = JudgingService(AnalystAgent(llm), BlindJudgeAgent(llm), BrokenProvider(), 10)
    with pytest.raises(JudgingError, match="Only 0 verified references"):
        service.evaluate(b"pdf", b"// TARGET\nint main() { return 0; }")
    assert len(llm.calls) == 3


def test_clarity_agent_uses_five_point_integer_criteria_without_std_or_editorial():
    llm = FakeLLM()
    interpretation = InterpretationAgent(llm).interpret("A statement")
    result = ClarityJudgeAgent(llm).score("A statement", interpretation)
    assert result["score"] == 5
    assert result["weighted_score"] == 4.6
    assert result["passing_score"] == 3
    assert sum(item["weight"] for item in result["criteria"]) == 1
    assert all(item["max_score"] == 5 and type(item["score"]) is int for item in result["criteria"])
    assert llm.calls[0][1] == {"statement": "A statement"}
    assert set(llm.calls[1][1]) == {"original_statement", "neutral_statement",
                                    "unresolved_ambiguities"}


def test_clarity_weighted_score_can_fail_without_changing_difficulty_score():
    class LowClarityLLM(FakeLLM):
        def ask_json(self, system, payload):
            if "statement clarity judge" in system:
                return {"scores": {"objective_io": 2, "language": 4, "information_focus": 2,
                                    "background_independence": 1},
                        "notes": {key: "核心术语未定义。" for key in
                                  ("objective_io", "language", "information_focus", "background_independence")},
                        "summary": "关键任务需要猜测。", "issues": ["核心术语未定义"]}
            return super().ask_json(system, payload)

    result = ClarityJudgeAgent(LowClarityLLM()).score("An unclear task", {
        "neutral_statement": "输入一个整数，输出指定操作后的结果。",
        "unresolved_ambiguities": ["操作规则在题面中未写明"]})
    assert result["score"] == 2
    assert result["weighted_score"] == 2.4
    assert result["verdict"] == "weak reject"
    assert result["passed"] is False


def test_interpreter_treats_none_marker_as_empty():
    class EmptyLLM:
        def ask_json(self, system, payload):
            return {"neutral_statement": "输入若干整数，计算所有整数的总和并输出结果。",
                    "unresolved_ambiguities": ["None"]}

    result = InterpretationAgent(EmptyLLM()).interpret("statement")
    assert result["unresolved_ambiguities"] == []


def test_english_agent_prose_is_localized_without_changing_scores_or_tags():
    class EnglishLLM:
        def __init__(self):
            self.translation_payloads = []

        def ask_json(self, system, payload):
            if "Translate the supplied explanatory text" in system:
                self.translation_payloads.append(payload)
                return {"texts": {key: f"中文说明：{value}" for key, value in payload["texts"].items()}}
            if "select 1-4" in system:
                return {"summary": "Count reachable vertices", "algorithm": "BFS",
                        "complexity": "O(n+m)", "tags": ["graphs"]}
            if "neutral interpretation agent" in system:
                return {"neutral_statement": "Read a graph and count all vertices reachable from a start vertex.",
                        "unresolved_ambiguities": ["The graph direction is unspecified."]}
            if "statement clarity judge" in system:
                return {"scores": {key: 4 for key in ("objective_io", "language", "information_focus", "background_independence")},
                        "notes": {key: "The task is clear." for key in
                                  ("objective_io", "language", "information_focus", "background_independence")},
                        "summary": "The input rules are clear.", "issues": ["One term is undefined."]}
            ids = [problem["id"] for problem in payload["problems"]]
            return {"order": ids, "reason": "The target needs only BFS."}

    llm = EnglishLLM()
    analysis = AnalystAgent(llm).analyze("statement", "int main() {}")
    interpretation = InterpretationAgent(llm).interpret("statement")
    clarity = ClarityJudgeAgent(llm).score("statement", interpretation)
    order, reason = BlindJudgeAgent(llm).rank([
        BlindProblem(f"p_{i}", "statement", "int main() {}") for i in range(11)])

    assert analysis.tags == ("graphs",)
    assert all("中文说明" in value for value in (analysis.summary, analysis.algorithm, analysis.complexity))
    assert "中文说明" in interpretation["neutral_statement"]
    assert "中文说明" in interpretation["unresolved_ambiguities"][0]
    assert clarity["score"] == 4
    assert "中文说明" in clarity["summary"]
    assert all("中文说明" in item["reason"] for item in clarity["criteria"])
    assert "中文说明" in clarity["issues"][0]
    assert order == [f"p_{i}" for i in range(11)]
    assert "中文说明" in reason
    assert all(set(item) == {"texts"} for item in llm.translation_payloads)
    assert all("standard_solution" not in json.dumps(item) for item in llm.translation_payloads)


def test_gpt6_chat_request_omits_unsupported_temperature():
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert ChatAgent(api_key="test", model="gpt-6-sol", client=client).ask_json("system", {"x": 1}) == {"ok": True}
    assert requests[0]["model"] == "gpt-6-sol"
    assert "temperature" not in requests[0]
    assert requests[0]["response_format"] == {"type": "json_object"}


def test_one_denied_reference_page_uses_another_candidate(monkeypatch):
    monkeypatch.setattr("autojudger.service.extract_pdf", lambda _: "A complete target statement. " * 10)

    class PartiallyDeniedProvider(FakeProvider):
        def hydrate(self, p):
            if p["contestId"] == 100:
                raise SourceAccessDenied("one page denied")
            return super().hydrate(p)

    service = JudgingService(AnalystAgent(FakeLLM()), BlindJudgeAgent(FakeLLM()),
                             PartiallyDeniedProvider(), 10)
    result = service.evaluate(b"pdf", b"// TARGET\nint main() { return 0; }")
    assert result["reference_count"] == 10
    assert all(item.get("codeforces_id") != "100A" for item in result["ranking"])


def test_uploaded_problem_is_excluded_from_references(monkeypatch):
    statement = "A complete target statement with input, output and constraints. " * 8
    source = "// TARGET\nint main() { return 0; }"
    monkeypatch.setattr("autojudger.service.extract_pdf", lambda _: statement)

    class ProviderWithSelf(FakeProvider):
        def hydrate(self, p):
            if p["contestId"] == 100:
                return Reference(100, "A", "Uploaded problem", 800, ("graphs",),
                                 statement.replace("constraints. ", "constraints.\n"), source)
            return super().hydrate(p)

    service = JudgingService(AnalystAgent(FakeLLM()), BlindJudgeAgent(FakeLLM()),
                             ProviderWithSelf(), 10)
    result = service.evaluate(b"pdf", source.encode())
    assert result["reference_count"] == 10
    assert result["excluded_self_references"] == ["100A"]
    assert all(item.get("codeforces_id") != "100A" for item in result["ranking"])


def test_content_duplicate_detection_tolerates_pdf_formatting():
    statement = "Read an array and output the sum of its elements. " * 12
    ref = Reference(123, "A", "same", 800, ("math",),
                    "Title\n" + statement.replace("elements. ", "elements.\n"), "different code")
    assert same_problem(statement, "original code", ref)


def test_candidates_span_ratings():
    problems = FakeProvider().catalog()
    selected = select_candidates(problems, ("graphs",), 10)
    assert len({(p["contestId"], p["index"]) for p in selected}) == len(selected)
    assert selected[0]["rating"] == 800
    assert selected[9]["rating"] == 2450


def test_sparse_verified_pool_is_not_used_to_score():
    pool = [Reference(100 + i, "A", "x", 800 if i < 7 else [2200, 2600, 3500][i - 7],
                      ("math",), "s", "code") for i in range(10)]
    assert balanced_references(pool, 10) is None


def test_pdf_rejects_scanned_or_empty_content():
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    stream = io.BytesIO()
    writer.write(stream)
    with pytest.raises(JudgingError, match="little extractable text"):
        extract_pdf(stream.getvalue())


def test_pdf_rejects_more_than_ten_pages():
    writer = PdfWriter()
    for _ in range(11):
        writer.add_blank_page(width=200, height=200)
    stream = io.BytesIO()
    writer.write(stream)
    with pytest.raises(JudgingError, match="10 pages"):
        extract_pdf(stream.getvalue())


def test_official_editorial_code_is_matched_to_exact_problem():
    editorial = """<div class="ttypography">
      <p><a href="/contest/1234/problem/A">1234A</a></p>
      <div class="spoiler"><b>Solution</b><pre><code>#include &lt;iostream&gt;\nint main() { std::cout &lt;&lt; 1; return 0; }</code></pre></div>
      <p><a href="/contest/1234/problem/B1">1234B1</a></p>
      <div class="spoiler"><b>Solution</b><pre><code>#include &lt;iostream&gt;\nint main() { std::cout &lt;&lt; 2; return 0; }</code></pre></div>
      </div>"""

    def handler(request):
        if "/api/blogEntry.view" in str(request.url):
            return httpx.Response(200, json={"status": "OK", "result": {"content": editorial}})
        return httpx.Response(200, text="""<div class="problem-statement"><p>Problem statement with constraints and examples. Problem statement with constraints and examples. Problem statement with constraints and examples.</p></div><a href="/blog/entry/70233">Tutorial</a>""")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = CodeforcesProvider(client=client, min_api_interval=0)
    problem = {"contestId": 1234, "index": "A", "name": "A", "rating": 800, "tags": ["math"]}
    reference = provider.hydrate(problem)
    assert "<< 1" in reference.source
    assert "<< 2" not in reference.source
    assert reference.editorial_url == "https://codeforces.com/blog/entry/70233"


def test_editorial_source_redirect_is_not_followed_to_private_network():
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://127.0.0.1:8765/health"})

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    provider = CodeforcesProvider(client=client, min_api_interval=0)
    with pytest.raises(JudgingError, match="redirected"):
        provider._linked_source({"contestId": 1234, "index": "A"},
                                "https://raw.githubusercontent.com/example/repo/main/std.cpp")
    assert len(requests) == 1


def test_monotone_calibration_repairs_rating_inversion():
    assert _monotone_probabilities([800, 1000, 1200], [1, 0, 1]) == [1, 0.5, 0.5]
    refs = {f"r{i}": Reference(100 + i, "A", "x", 800 + 200 * i, ("dp",), "s", "code")
            for i in range(10)}
    # The model places r2 ahead of the easier r1, but the calibrated score
    # must respect the rating ladder and report the inversion.
    order = ["r0", "r2", "r1", "target"] + [f"r{i}" for i in range(3, 10)]
    result = calibrate("target", refs, [order] * 3)
    assert result.score == 1300
    assert result.relative_percentile == 30.0
    assert result.rank == 4
    assert result.anchor_inversion_rate > 0
    assert all(a >= b for a, b in zip(result.probabilities, result.probabilities[1:]))


def test_calibrated_insert_ignores_anomalous_high_rating_win():
    refs = {f"r{i}": Reference(100 + i, "A", "x", 800 + 200 * i, ("dp",), "s", "code")
            for i in range(10)}
    order = ["r0", "r9", "target"] + [f"r{i}" for i in range(1, 9)]
    result = calibrate("target", refs, [order] * 3)
    assert result.raw_ranks == (3, 3, 3)
    assert result.rank == 2
    assert result.score == 900
    assert result.relative_percentile == 10.0
    assert result.anchor_inversion_rate > 0


def test_rating_estimate_extrapolates_only_at_ladder_edge():
    refs = {f"r{i}": Reference(100 + i, "A", "x", 800 + 200 * i, ("dp",), "s", "code")
            for i in range(10)}
    result = calibrate("target", refs, [[f"r{i}" for i in range(10)] + ["target"]] * 3)
    assert result.score == 2700
    assert result.rating_neighbors == (2600, None)
    assert result.relative_percentile == 100


def test_nonstandard_editorial_agent_extracts_only_verbatim_complete_code():
    code = '#include <iostream>\nint main() { int n; std::cin >> n; std::cout << n + 1; return 0; }'
    editorial = f'<div class="ttypography"><div class="problemTutorial" problemcode="1730A">Tutorial</div><div><code>{code.replace("<", "&lt;").replace(">", "&gt;")}</code></div><div class="problemTutorial" problemcode="1730B">Next</div></div>'

    class Locator:
        def ask_json(self, system, payload):
            assert payload["problem_code"] == "1730A"
            assert code in payload["section_text"]
            return {"kind": "embedded", "source": code, "url": ""}

    provider = CodeforcesProvider(client=httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"status": "OK", "result": {"content": editorial}}))),
        min_api_interval=0, extractor=EditorialExtractionAgent(Locator()))
    source, source_url, method = provider.editorial_source({"contestId": 1730, "index": "A"}, 99)
    assert source == code
    assert source_url is None
    assert method == "agent_verified_embedded"

    class Hallucinator:
        def ask_json(self, system, payload):
            return {"kind": "embedded", "source": code + " invented", "url": ""}

    provider.extractor = EditorialExtractionAgent(Hallucinator())
    with pytest.raises(JudgingError, match="invented or altered"):
        provider.editorial_source({"contestId": 1730, "index": "A"}, 99)


def test_partial_editorial_solution_is_not_treated_as_std():
    assert not CodeforcesProvider._complete_program(
        "namespace solution { void solve() { int x; cin >> x; cout << x; } }")
    assert not CodeforcesProvider._complete_program(
        "class Solution:\n    def solve(self):\n        n = int(input())\n        print(n)\n")


def test_editorial_template_and_problem_fragment_form_complete_program():
    editorial = '''<div class="ttypography">
      <div class="spoiler"><b class="spoiler-title">C++ template</b><pre>
      #include &lt;iostream&gt;
      namespace solution {
      }
      int main() { solution::solve(); }
      </pre></div>
      <p><a href="/contest/2032/problem/A">2032A</a></p>
      <div class="spoiler"><b class="spoiler-title">C++ solution</b><pre>
      namespace solution {
      void solve() { std::cout &lt;&lt; 42; }
      }
      </pre></div>
      <p><a href="/contest/2032/problem/B">2032B</a></p>
      </div>'''
    provider = CodeforcesProvider(client=httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"status": "OK", "result": {"content": editorial}}))),
        min_api_interval=0)
    source, source_url, method = provider.editorial_source({"contestId": 2032, "index": "A"}, 10)
    assert method == "editorial_template_composed"
    assert source_url is None
    assert "void solve() { std::cout << 42; }" in source
    assert "int main() { solution::solve(); }" in source
    assert source.count("namespace solution") == 1


def test_official_solution_preferred_over_longer_alternative():
    official = '#include <iostream>\nint main() { std::cout << 1; return 0; }'
    alternate = '#include <iostream>\nint main() { std::cout << 2; std::cout << 2; return 0; }'
    editorial = f'''<div class="ttypography"><p><a href="/contest/500/problem/A">500A</a></p>
      <div class="spoiler"><b class="spoiler-title">Alternative brute force</b><pre>{alternate.replace("<", "&lt;").replace(">", "&gt;")}</pre></div>
      <div class="spoiler"><b class="spoiler-title">Official solution</b><pre>{official.replace("<", "&lt;").replace(">", "&gt;")}</pre></div></div>'''
    provider = CodeforcesProvider(client=httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"status": "OK", "result": {"content": editorial}}))),
        min_api_interval=0)
    source, _, _ = provider.editorial_source({"contestId": 500, "index": "A"}, 10)
    assert source == official
