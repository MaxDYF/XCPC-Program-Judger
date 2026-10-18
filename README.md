# XCPC-ProblemJudger

**XCPC题目评分器** evaluates statement clarity and estimates Codeforces difficulty for ICPC/CCPC style problems.

[English](README.md) · [中文文档](docs/README-CN.md) · [Method](#method) · [Validation](#validation) · [MIT License](LICENSE)

## Abstract

Submit a complete problem statement PDF and reference solution (`std`) source file. The pipeline returns an integer 0–5 clarity grade and an estimated Codeforces rating. It retrieves 5–20 similarly tagged references across a rating gradient, reads their statements and author editorials, and ranks anonymous problem and source pairs. Reports and a history page are available in the web UI. Analysis is written in Chinese; source quotations may retain their language.

The rating is an estimate, not an official Codeforces rating or a correctness proof for the uploaded solution.

## Method

1. **Analysis:** An agent summarizes the task, `std` algorithm and complexity, and likely Codeforces tags.
2. **Clarity:** An interpretation agent reads only the statement and restates the task without assumed cultural background. A separate judge receives the statement and interpretation, never the solution or editorial. It rates task/input/output (20%), language (35%), information density (10%), and background independence (35%), each as an integer 0–5. The rounded weighted grade maps 0–5 to strong reject, reject, weak reject, weak accept, accept, and strong accept; 3 is the acceptance threshold. It evaluates whether a contestant understands what to do, not how to solve it.
3. **References:** Codeforces metadata supplies tags and ratings. The service samples a gradient, excludes the submitted problem itself, and retrieves statements and author editorials. Deterministic extraction reads complete editorial programs; an extraction agent handles unusual layouts only when needed. Its output must match source text or an allowed source link. Incomplete references are skipped. Up to four candidates are fetched concurrently.
4. **Blind ranking:** The independent ranking agent receives anonymous statements and programs without IDs, tags, ratings, URLs, retrieval notes, or clarity scores. It has no web tool. The default is three independent rounds.
5. **Calibration:** Pairwise votes against references are fitted to the rating gradient with a minimum-conflict insertion and monotonic regression. The neighboring reference ratings yield a Codeforces rating estimate rounded to a 100-point band. Reports disclose raw ranks, inversions, equally optimal insertion positions, and edge extrapolation. A 0–100 gradient position is supplementary, not the difficulty score.

Codeforces ratings are empirical; individual reference difficulty can disagree with rating order. Blind input isolation cannot erase model prior knowledge or identifying clues inside statements and code. The report exposes disagreement rather than implying perfect order.

## Installation

Python 3.11 or newer is required.

```bash
python -m venv .venv
.venv/bin/pip install -e '.[test]'
export OPENAI_API_KEY='replace-with-your-own-key'
export OPENAI_BASE_URL='https://your-compatible-endpoint.example/v1'
```

`OPENAI_API_KEY` is read from the process environment and held in memory for calls to the configured Chat Completions endpoint. Do not commit a filled environment file. Analysis, interpretation, extraction, and formatting default to `gpt-6-luna`; blind ranking (`JUDGE_MODEL`) and clarity grading (`CLARITY_MODEL`) default to `gpt-6-sol`. `OPENAI_MODEL`, `ANALYST_MODEL`, `INTERPRETER_MODEL`, `EXTRACTOR_MODEL`, and `FORMAT_MODEL` override individual roles. The endpoint needs JSON mode and enough context for complete references. Uploaded statements and solutions are not silently truncated.

### CLI

```bash
.venv/bin/xcpc-problemjudger problem.pdf std.cpp --references 12 --output report.json
```

### Web app and HTTP API

```bash
deploy/run-sandbox.sh
# Open http://127.0.0.1:8765/
curl -H 'X-XCPC-ProblemJudger-Request: 1' \
  -F statement_pdf=@problem.pdf -F std_file=@std.cpp -F reference_count=12 \
  http://127.0.0.1:8765/evaluate
```

The UI uses `POST /jobs` and polls `GET /jobs/{job_id}`. Its task ID is kept in browser session storage, so refreshing that tab resumes the progress view; the server keeps evaluating even if the tab closes. `GET /api/jobs` lists queued and running tasks with progress metadata, and the history page refreshes this list every four seconds. In-progress detail pages show the current phase. `POST /evaluate` is the synchronous JSON endpoint. `GET /api/history` and `GET /api/history/{id}` show completed reports. `AUTOJUDGER_HISTORY_DB` selects a private SQLite path; the default is `data/history.sqlite3`. The database stores reports, not uploaded PDF or `std` files. **Completed reports and live task states are publicly readable**, so operators must treat them as public. In-progress tasks are held only in server memory; restarting the process interrupts them and clears their status.

Public access needs no login. PDFs are limited to 10 pages and 20 MiB, source files to 1 MiB. One source IP may submit five PDFs per sliding ten-minute window across both submission endpoints. Every write needs `X-XCPC-ProblemJudger-Request: 1` and passes a same-origin check. Temporary jobs are bounded in number and lifetime. Rate counters live in one process; multi-instance deployments need shared or edge-side limiting.

## Deployment and privacy

Bind to `127.0.0.1` and point a Cloudflare Tunnel hostname to that loopback address. Set `AUTOJUDGER_PUBLIC_HOST=judge.example.org` to the exact hostname for your deployment; the default Host allowlist accepts loopback names only. `deploy/run-sandbox.sh` uses Bubblewrap to expose a read-only runtime and source tree, a private `/tmp`, and only `data/` as writable. It shares the host network. The [Chinese deployment notes](docs/README-CN.md#cloudflare-tunnel-公网发布与隔离) include a read-only Docker invocation.

The configured model endpoint receives uploaded statements and sources as required for evaluation. Review its data policy before processing confidential problems. Codeforces and allowed source links receive ordinary retrieval requests.

The repository excludes environment files, private keys, uploads, `data/`, reports, manifests, PDFs, and SQLite databases including journal files. `scripts/check_release.py` scans the **actual Git index** before publishing; `.gitignore` alone cannot protect a force-added file. Audit previous commits before publishing an existing repository history.

## Reference provenance

Live mode uses Codeforces `problemset.problems`, problem pages, linked author Tutorials, and `blogEntry.view`. Editorials may have full code, per-problem sections, submission links, snippets, or shared templates. The extractor verifies literal code or source links and skips incomplete programs. Retrieved editorial programs are not formal platform-provided `std` files. Site restrictions and layout changes can reduce coverage.

`REFERENCE_MANIFEST` may point to an operator-reviewed JSON corpus **outside** the repository. Each entry needs `contestId`, `index`, `name`, `rating`, `tags`, `statement`, `source`, and `editorialUrl`. Redistribute third-party text and code only with appropriate rights. The service refuses difficulty estimates when rating coverage and density requirements are unmet.

## Validation

```bash
.venv/bin/pytest -q
.venv/bin/python scripts/check_release.py
PYTHONPATH=src .venv/bin/python scripts/verify_codeforces_sample.py
```

Unit tests use fake agents and references to exercise scoring weights, input isolation, calibration, editorial extraction, and PDF validation without paid model calls. The live sample script needs model credentials. On 2026-10-18 it attempted 20 Codeforces references and verified 10 across CF 800–2200. The [metadata-only audit](validation/codeforces-sample.json) contains URLs, lengths, hashes, and failure reasons, not statements, code, or keys. A [historical pipeline smoke result](validation/end-to-end-smoke.json) used an earlier 10-point clarity rubric and is not a current 5-point result. Neither sample establishes population-level rating accuracy.

## Research use and license

For reproducible comparisons, record the application commit, model and endpoint versions, ranking rounds, reference IDs and ratings, and retrieval date. Human review and fresh reference retrieval remain necessary. See [contribution guidelines](CONTRIBUTING.md), [security reporting](SECURITY.md), and [citation metadata](CITATION.cff).

Released under the [MIT License](LICENSE). Codeforces statements, editorials, and source programs retain their original rights; full copies are not included here.
