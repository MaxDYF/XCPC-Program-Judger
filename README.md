<div align="center">

# XCPC-ProblemJudger

**Statement clarity review and Codeforces difficulty estimation for ICPC/CCPC-style problems**

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![CI](https://github.com/MaxDYF/XCPC-Program-Judger/actions/workflows/ci.yml/badge.svg)](https://github.com/MaxDYF/XCPC-Program-Judger/actions/workflows/ci.yml)
[![MIT License](https://img.shields.io/badge/License-MIT-2da891)](LICENSE)

[简体中文](docs/README-CN.md) · [Quick Start](#-quick-start)

</div>

---

## 🌟 Highlights

- Upload a statement PDF and reference solution to receive a **0–5 statement clarity score** (one decimal place; verdict rounded to the nearest integer) and an **estimated Codeforces rating**.
- Calibrate difficulty against **5–20 Codeforces problems** with similar tags and author editorials; exclude the submitted problem if it appears among the references.
- Use independent agents for statement review and anonymous difficulty ranking. The report shows where model rankings disagree with the reference rating gradient.
- Track running evaluations and browse completed reports in the web app. Reports are written in Chinese; quotations may retain their original language.

## 🚀 Quick Start

Requires Python 3.11+ and Bubblewrap.

```bash
git clone https://github.com/MaxDYF/XCPC-Program-Judger.git
cd XCPC-Program-Judger
python -m venv .venv
.venv/bin/pip install -e .
export OPENAI_API_KEY='replace-with-your-own-key'
export OPENAI_BASE_URL='https://your-compatible-endpoint.example/v1'
deploy/run-sandbox.sh
```

Open `http://127.0.0.1:8765/`.

## ⚙️ Configuration

The model endpoint must support Chat Completions JSON responses. Set `AUTOJUDGER_PUBLIC_HOST` to your exact public hostname when using a tunnel. Blind ranking and clarity grading default to `gpt-6-sol`; other agent roles default to `gpt-6-luna`. Model choices can be overridden through the environment variables in [`src/autojudger/api.py`](src/autojudger/api.py).

## 🔒 Data and Deployment

The API key is read from the process environment. Uploaded PDFs and source files are not stored in the history database; **completed reports and live task states are public** through the web app. The default SQLite database is `data/history.sqlite3`, which is excluded from Git along with environment files and uploads.

Public submissions are limited to PDFs of 10 pages and 20 MiB, source files of 1 MiB, and five PDF submissions per IP per ten minutes. The included launcher keeps the service on `127.0.0.1` inside a Bubblewrap filesystem sandbox. A container build is available in [Dockerfile](Dockerfile). Uploaded material is sent to the configured model service for evaluation.

## 📘 License

Released under the [MIT License](LICENSE). Codeforces problem content retains its original rights.
