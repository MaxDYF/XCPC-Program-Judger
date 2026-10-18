from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from .agents import EditorialExtractionAgent
from .models import JudgingError, Reference, SourceAccessDenied


class CodeforcesProvider:
    """Fetch public problem metadata, statements and official editorial code."""

    def __init__(self, client: httpx.Client | None = None, min_api_interval: float = 2.1,
                 extractor: EditorialExtractionAgent | None = None):
        self.client = client or httpx.Client(timeout=30, follow_redirects=False,
                                               headers={"User-Agent": "XCPC-ProblemJudger/0.1 (research tool)"})
        self.min_api_interval = min_api_interval
        self._last_api = 0.0
        self._api_lock = threading.Lock()
        self._blog_lock = threading.Lock()
        self._blog_cache: dict[int, str] = {}
        self.extractor = extractor

    def api(self, method: str, params: dict[str, Any] | None = None) -> Any:
        with self._api_lock:
            elapsed = time.monotonic() - self._last_api
            if elapsed < self.min_api_interval:
                time.sleep(self.min_api_interval - elapsed)
            try:
                response = self.client.get(f"https://codeforces.com/api/{method}", params=params)
                self._last_api = time.monotonic()
                response.raise_for_status()
                data = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise JudgingError(f"Codeforces API request failed ({method}): {exc}") from exc
        if data.get("status") != "OK":
            raise JudgingError(f"Codeforces API failed ({method}): {data.get('comment', 'unknown error')}")
        return data["result"]

    def catalog(self) -> list[dict[str, Any]]:
        return self.api("problemset.problems")["problems"]

    def _problem_page(self, problem: dict[str, Any]) -> BeautifulSoup:
        url = f"https://codeforces.com/problemset/problem/{problem['contestId']}/{problem['index']}"
        try:
            response = self.client.get(url, params={"locale": "en", "mobile": "true"})
            if response.status_code in (401, 403, 429):
                raise SourceAccessDenied(f"Codeforces denied access to problem pages ({response.status_code}); use a reviewed reference manifest")
            response.raise_for_status()
        except SourceAccessDenied:
            raise
        except httpx.HTTPError as exc:
            raise JudgingError(f"Problem page unavailable: {url}: {exc}") from exc
        return BeautifulSoup(response.text, "html.parser")

    def statement(self, problem: dict[str, Any], soup: BeautifulSoup | None = None) -> str:
        url = f"https://codeforces.com/problemset/problem/{problem['contestId']}/{problem['index']}"
        soup = soup or self._problem_page(problem)
        node = soup.select_one(".problem-statement")
        if node is None:
            raise JudgingError(f"Problem statement missing: {url}")
        for junk in node.select("script, style, .title, .time-limit, .memory-limit, .input-file, .output-file"):
            junk.decompose()
        for tag in node.select("sup, sub"):
            marker = "^" if tag.name == "sup" else "_"
            tag.replace_with(f"{marker}{{{tag.get_text('', strip=True)}}}")
        for img in node.select("img"):
            if not img.get("alt"):
                raise JudgingError(f"Problem statement contains an image without text description: {url}")
            img.replace_with(f"[image: {img['alt']}]")
        text = node.get_text("\n", strip=True)
        if len(text) < 100:
            raise JudgingError(f"Problem statement too short: {url}")
        return text

    @staticmethod
    def _editorial_id(soup: BeautifulSoup) -> int:
        for link in soup.select('a[href*="/blog/entry/"]'):
            href = link.get("href", "")
            label = f"{link.get_text(' ', strip=True)} {link.get('title', '')}".lower()
            match = re.search(r"/blog/entry/(\d+)", href)
            if match and ("tutorial" in label or "editorial" in label):
                return int(match.group(1))
        raise JudgingError("No official tutorial link on the problem page")

    @staticmethod
    def _complete_program(code: str) -> bool:
        if len(code.strip()) < 50:
            return False
        if re.search(r"\b(?:int|signed|auto|void)\s+main\s*\(", code):
            return True
        if re.search(r"\b(?:fn|func)\s+main\s*\(", code):
            return True
        if "static void main" in code and re.search(r"\bclass\s+\w+", code):
            return True
        if re.search(r"if\s+__name__\s*==\s*['\"]__main__['\"]", code):
            return True
        # Some Python editorials use a complete top-level script.
        if "class Solution" not in code and re.search(r"\b(?:input|sys\.stdin\.read|sys\.stdin\.buffer)\s*\(", code) and re.search(r"\bprint\s*\(", code):
            return True
        return False

    @staticmethod
    def _code_priority(node) -> tuple[int, int]:
        spoiler = node.find_parent(class_="spoiler")
        title = spoiler.select_one(".spoiler-title") if spoiler else None
        label = title.get_text(" ", strip=True).lower() if title else ""
        preference = (5 if "official" in label or "jury" in label else 0)
        preference += 3 if "solution" in label or "code" in label else 0
        preference -= 4 if any(word in label for word in ("template", "brute", "alternative")) else 0
        return preference, len(node.get_text("", strip=False))

    @classmethod
    def _compose_editorial_template(cls, root: BeautifulSoup, section: list) -> str | None:
        """Combine an explicit C++ template and its problem-specific namespace."""
        fragments = [pre.get_text("", strip=False).strip()
                     for item in section for pre in item.select("pre")]
        fragments = [code for code in fragments if re.fullmatch(
            r"\s*namespace\s+solution\s*\{[\s\S]*\}\s*", code) and not cls._complete_program(code)]
        if not fragments:
            return None
        templates = []
        for pre in root.select("pre"):
            spoiler = pre.find_parent(class_="spoiler")
            title = spoiler.select_one(".spoiler-title") if spoiler else None
            if title and "c++ template" in title.get_text(" ", strip=True).lower():
                template = pre.get_text("", strip=False)
                if cls._complete_program(template):
                    templates.append(template)
        if len(templates) != 1:
            return None
        template = templates[0]
        opening = re.search(r"(?m)^(?P<indent>[ \t]*)namespace\s+solution\s*\{", template)
        if opening is None:
            return None
        closing = re.search(rf"(?m)^{re.escape(opening.group('indent'))}\}}[ \t]*$",
                            template[opening.end():])
        if closing is None:
            return None
        end = opening.end() + closing.end()
        # The two components are both verbatim from the same official editorial.
        composed = template[:opening.start()] + max(fragments, key=len) + template[end:]
        return composed if cls._complete_program(composed) else None

    @staticmethod
    def _section_nodes(root: BeautifulSoup, problem: dict[str, Any]) -> list:
        contest_id, index = problem["contestId"], problem["index"]
        code = f"{contest_id}{index}"
        problem_path = re.compile(rf"/(?:contest/{contest_id}/problem/{re.escape(index)}|problemset/problem/{contest_id}/{re.escape(index)})(?:$|[?#])")
        markers = [a for a in root.select("a[href]") if problem_path.search(a.get("href", ""))]
        markers.extend(root.select(f'.problemTutorial[problemcode="{code}"]'))
        for marker in markers:
            block = marker
            while block.parent is not None and block.parent is not root:
                block = block.parent
            section = []
            for sibling in block.next_siblings:
                if not getattr(sibling, "name", None):
                    continue
                other_links = sibling.select('a[href*="/problem/"]')
                if other_links and any(re.search(r"/(?:contest/\d+/problem|problemset/problem/\d+)/[A-Z]\d*", a.get("href", "")) for a in other_links):
                    break
                other_tutorials = sibling.select(".problemTutorial[problemcode]")
                if (sibling.get("problemcode") and sibling.get("problemcode") != code) or any(
                    t.get("problemcode") != code for t in other_tutorials):
                    break
                section.append(sibling)
            if section:
                return section
        return []

    def _linked_source(self, problem: dict[str, Any], link: str) -> str:
        url = urljoin("https://codeforces.com", link)
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.username or parsed.password:
            raise JudgingError("Editorial source link is not an allowed HTTPS URL")
        if parsed.hostname == "codeforces.com":
            contest_id = problem["contestId"]
            if not re.fullmatch(rf"/(?:contest/{contest_id}/submission|problemset/submission/{contest_id})/\d+", parsed.path):
                raise JudgingError("Editorial submission link is not for this contest")
        elif parsed.hostname == "raw.githubusercontent.com":
            if not re.search(r"\.(?:cpp|cc|c|py|java|rs|go)$", parsed.path, re.I):
                raise JudgingError("Editorial source link is not a source file")
        elif parsed.hostname == "github.com":
            match = re.fullmatch(r"/([^/]+)/([^/]+)/blob/([^/]+)/(.+\.(?:cpp|cc|c|py|java|rs|go))", parsed.path, re.I)
            if not match:
                raise JudgingError("Editorial GitHub link is not a source file")
            url = f"https://raw.githubusercontent.com/{match.group(1)}/{match.group(2)}/{match.group(3)}/{match.group(4)}"
        else:
            raise JudgingError("Editorial source link host is unsupported")
        try:
            response = self.client.get(url, params={"locale": "en", "mobile": "true"} if parsed.hostname == "codeforces.com" else None,
                                       follow_redirects=False)
            if 300 <= response.status_code < 400:
                raise JudgingError("Editorial source redirected; source URL was rejected")
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise JudgingError(f"Editorial-linked source unavailable: {exc}") from exc
        if urlparse(str(response.url)).hostname not in ("codeforces.com", "raw.githubusercontent.com"):
            raise JudgingError("Editorial source redirected to an unsupported host")
        if len(response.content) > 200000:
            raise JudgingError("Editorial-linked source is too large")
        if parsed.hostname == "codeforces.com":
            node = BeautifulSoup(response.text, "html.parser").select_one("#program-source-text")
            if node is None:
                raise JudgingError("Editorial-linked submission has no readable source")
            code = node.get_text("", strip=False).strip()
        else:
            code = response.text.strip()
        if not self._complete_program(code):
            raise JudgingError("Editorial-linked source is not a complete program")
        return code

    def editorial_source(self, problem: dict[str, Any], blog_id: int) -> tuple[str, str | None, str]:
        with self._blog_lock:
            if blog_id not in self._blog_cache:
                entry = self.api("blogEntry.view", {"blogEntryId": blog_id})
                content = entry.get("content")
                if not isinstance(content, str) or not content:
                    raise JudgingError(f"Editorial {blog_id} has no readable content")
                self._blog_cache[blog_id] = content
            editorial_html = self._blog_cache[blog_id]
        soup = BeautifulSoup(editorial_html, "html.parser")
        root = soup.select_one(".ttypography") or soup
        contest_id, index = problem["contestId"], problem["index"]
        section = self._section_nodes(root, problem)
        if not section:
            raise JudgingError(f"Official editorial {blog_id} has no identifiable section for {contest_id}{index}")
        candidates = [(self._code_priority(pre), pre.get_text("", strip=False).strip())
                      for item in section for pre in item.select("pre, textarea")]
        complete = [(priority, code) for priority, code in candidates if self._complete_program(code)]
        if complete:
            return max(complete, key=lambda candidate: candidate[0])[1], None, "editorial_embedded"
        composed = self._compose_editorial_template(root, section)
        if composed is not None:
            return composed, None, "editorial_template_composed"
        if self.extractor is not None:
            section_text = "\n".join(item.get_text("", strip=False) for item in section)
            links = list(dict.fromkeys(urljoin("https://codeforces.com", a["href"])
                                       for item in section for a in item.select("a[href]")))
            found = self.extractor.locate(f"{contest_id}{index}", section_text, links)
            if found["kind"] == "embedded" and self._complete_program(found["source"]):
                return found["source"], None, "agent_verified_embedded"
            if found["kind"] == "link":
                code = self._linked_source(problem, found["url"])
                return code, found["url"], "editorial_linked"
        raise JudgingError(f"Official editorial {blog_id} has no verifiable complete solution for {contest_id}{index}")

    def hydrate(self, problem: dict[str, Any]) -> Reference:
        soup = self._problem_page(problem)
        statement = self.statement(problem, soup)
        blog_id = self._editorial_id(soup)
        source, source_url, source_method = self.editorial_source(problem, blog_id)
        return Reference(problem["contestId"], problem["index"], problem["name"],
                         problem["rating"], tuple(problem["tags"]), statement, source,
                         editorial_url=f"https://codeforces.com/blog/entry/{blog_id}",
                         source_url=source_url, source_method=source_method)


class ManifestProvider:
    """Offline corpus of previously captured, reviewed Codeforces references."""

    def __init__(self, path: str | Path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise JudgingError("Reference manifest must be a JSON array")
        self.problems = data

    def catalog(self) -> list[dict[str, Any]]:
        return self.problems

    def hydrate(self, problem: dict[str, Any]) -> Reference:
        required = ("contestId", "index", "name", "rating", "tags", "statement", "source")
        if any(k not in problem for k in required):
            raise JudgingError("Reference manifest entry is incomplete")
        if not isinstance(problem["rating"], int) or problem["rating"] < 100:
            raise JudgingError("Reference manifest has an invalid rating")
        if len(problem["statement"].strip()) < 30 or len(problem["source"].strip()) < 20:
            raise JudgingError("Reference manifest has an empty statement or source")
        return Reference(problem["contestId"], problem["index"], problem["name"],
                         problem["rating"], tuple(problem["tags"]), problem["statement"],
                         problem["source"], problem.get("submissionId"), problem.get("editorialUrl"),
                         problem.get("sourceUrl"), "manifest_reviewed")


def select_candidates(catalog: list[dict[str, Any]], tags: tuple[str, ...], count: int) -> list[dict[str, Any]]:
    """Interleave primary choices and fallbacks across CF rating bands."""
    if not 5 <= count <= 20:
        raise ValueError("reference count must be 5-20")
    wanted = set(tags)
    eligible = [p for p in catalog if isinstance(p.get("rating"), int)
                and p["rating"] >= 800
                and isinstance(p.get("contestId"), int) and isinstance(p.get("index"), str)
                and "interactive" not in p.get("tags", [])
                and wanted.intersection(p.get("tags", []))]
    if len(eligible) < count:
        raise JudgingError(f"Only {len(eligible)} Codeforces problems match the inferred tags; need {count}")
    low, high = min(p["rating"] for p in eligible), max(p["rating"] for p in eligible)
    targets = [low + (high - low) * i / (count - 1) for i in range(count)]
    # Each pass tries one problem per rating band. This keeps an inaccessible
    # editorial from causing every fallback to come from the crowded 800 band.
    groups = [sorted(eligible, key=lambda p: (abs(p["rating"] - target),
                    abs(p["contestId"] - 1900),
                    -len(wanted.intersection(p.get("tags", [])))))[:12]
              for target in targets]
    used: set[tuple[int, str]] = set()
    output: list[dict[str, Any]] = []
    for attempt in range(12):
        for group in groups:
            if attempt >= len(group):
                continue
            p = group[attempt]
            key = (p["contestId"], p["index"])
            if key not in used:
                used.add(key)
                output.append(p)
    return output
