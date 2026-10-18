<div align="center">

# XCPC-ProblemJudger

**XCPC题目评分器：评估 ICPC/CCPC 题面清晰度，估计 Codeforces 难度分**

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](../pyproject.toml)
[![CI](https://github.com/MaxDYF/XCPC-Program-Judger/actions/workflows/ci.yml/badge.svg)](https://github.com/MaxDYF/XCPC-Program-Judger/actions/workflows/ci.yml)
[![MIT License](https://img.shields.io/badge/License-MIT-2da891)](../LICENSE)

[English](../README.md) · [快速开始](#-快速开始)

</div>

---

## 🌟 功能亮点

- 上传题面 PDF 和 std 源码，得到 **0–5 分题面清晰度**（总分保留一位小数，评价档位四舍五入）与**估计 Codeforces rating**。
- 选取 **5–20 道**标签相近且带有出题人题解的 Codeforces 题作为参照；抽到上传题本身时会排除。
- 题面评估与匿名难度排序由独立 agent 执行；报告展示模型排序与参考题 rating 梯度的分歧。
- 网页可查看进行中任务和已完成报告。分析文字使用中文，引用原文可保留原语言。

## 🚀 快速开始

需要 Python 3.11 或更新版本，以及 Bubblewrap。

```bash
git clone https://github.com/MaxDYF/XCPC-Program-Judger.git
cd XCPC-Program-Judger
python -m venv .venv
.venv/bin/pip install -e .
export OPENAI_API_KEY='replace-with-your-own-key'
export OPENAI_BASE_URL='https://your-compatible-endpoint.example/v1'
deploy/run-sandbox.sh
```

打开 `http://127.0.0.1:8765/`。

## ⚙️ 配置

模型接口须支持 Chat Completions JSON 响应。使用公网隧道时，将 `AUTOJUDGER_PUBLIC_HOST` 设为准确域名。盲排和清晰度评分默认使用 `gpt-6-sol`，其他 agent 默认使用 `gpt-6-luna`；可通过 [`src/autojudger/api.py`](../src/autojudger/api.py) 中列出的环境变量覆盖。

## 🔒 数据与部署

API Key 从进程环境变量读取。上传的 PDF 和源码不保存在历史库中；**已完成报告和进行中任务状态在网页上公开可见**。默认 SQLite 数据库位于 `data/history.sqlite3`，数据库、环境文件和上传材料均被 Git 排除。

公开提交限制为 PDF 最多 10 页、20 MiB，源码最多 1 MiB；同一 IP 每 10 分钟最多提交 5 次 PDF。启动脚本将服务限制在 `127.0.0.1`，并使用 Bubblewrap 隔离文件系统。容器构建见 [Dockerfile](../Dockerfile)。评测时上传材料会发送给配置的模型服务。

## 📘 许可证

项目采用 [MIT License](../LICENSE)。Codeforces 题目内容仍归原权利人所有。
