# XCPC题目评分器 · XCPC-ProblemJudger

[English](../README.md) · [中文](README-CN.md) · [MIT License](../LICENSE)

给 ICPC 风格题目做**题面清晰度评估与难度标定**的服务。上传完整题面 PDF 与 std 源码后，在同一条 pipeline 中：

1. 分析 agent 理解题意、std 算法和复杂度，推断 Codeforces 标签。
2. 去背景理解 agent 只阅读原题面，写出不依赖文娱或其他题外知识的任务含义，并标明仅凭题面仍不明确的细节；这条评分链不接收 std 或题解。
3. 题面清晰度 agent **只接收原题面和去背景化含义，不接收 std 或题解**，以一般 ICPC/CCPC 选手合理掌握的竞赛常识为界判断题意是否清楚；超出这条线的内容，只有在题面未解释充分、确实妨碍理解任务时才扣分。任务与输入输出、语言表述、有效信息集中度、背景独立性各评 0–5 整数分，分别按 20%、35%、10%、35% 加权；加权原始分四舍五入为 0–5 的总分，3 分起为接受档位。0–5 依次对应 strong reject、reject、weak reject、weak accept、accept、strong accept。算法是否容易、做法是否清晰不计入此分。
4. 检索 Codeforces 题库，按标签匹配并从 CF 800 起、无固定难度上限的题目中分层取 5–20 道；最多 4 道候选题并行读取题面与官方题解，同一分档的候选作为备选依次尝试。收集时会比较上传题与参考题的题面和源码，遇到同一道题就排除并补选。验证后的参考题仍需满足 rating 的覆盖与密度要求，否则拒绝评分。实际覆盖范围受匹配题目和完整题解的供给限制。
5. 从题页的官方 Tutorial 链接进入 Codeforces 出题人题解，按题号提取该题的**完整程序**。常见代码块由规则解析；非标准排版交给提取 agent 定位，且必须逐字匹配原文或链接。缺少足量材料时返回错误，不生成虚构参考题。
6. 独立排序 agent 只接收匿名题面和源码，不能调用网页工具，输入不包含标签、rating、题号、URL、清晰度评分或检索分析。默认将用户题与参考题一同盲排 3 轮。
7. 统计盲排中“用户题比某参考题更难”的频率，在参考题的 rating 梯度上寻找使成对判断冲突最少的插入点；用单调回归整理互相矛盾的判断。**难度分是估计 Codeforces rating**：取插入位置两侧参考题 rating 的中点，按 100 分档取整。梯度两端以最近间距估算并明确标注。0–100 的梯度位置只作为辅助指标。报告展示模型原始顺位、顺序逆转率、同样最优的顺位区间和题解链接；这不是平台官方定级，也不校验 std 的正确性。题面清晰度和难度分不合并。

报告中的题意概述、算法与复杂度分析、去背景化题意、清晰度评语和盲排理由均使用中文。引用题面原句时可保留原语言，Codeforces 官方标签、题名、代码标识符、算法缩写和公式保留原样。若模型返回英文说明，服务会再调用模型翻译相应说明字段。

## 安装

需要 Python 3.11+：

```bash
python -m venv .venv
.venv/bin/pip install -e '.[test]'
export OPENAI_API_KEY='your-key'
export OPENAI_MODEL='gpt-6-luna'
```

`OPENAI_BASE_URL` 可指向兼容 Chat Completions 的服务。模型需支持 JSON mode 和足够大的上下文；20 道完整题面与源码可能超出模型上下文。服务不会静默截断上传的题面或 std。
对局域网 IP 的 `OPENAI_BASE_URL`，客户端会直接连接，以免系统代理拦截内网请求。不要把 API key 写进项目文件或验证报告。

默认分工：盲排 `JUDGE_MODEL` 与题面清晰度评分 `CLARITY_MODEL` 使用 `gpt-6-sol`；题目分析、去背景解释、题解格式提取与中文润色使用 `gpt-6-luna`。可用 `ANALYST_MODEL`、`INTERPRETER_MODEL`、`EXTRACTOR_MODEL`、`FORMAT_MODEL` 等环境变量单独覆盖。提取 agent 仅在规则解析失败时调用；它只能指出题解中逐字存在的代码或链接，服务端会再次检查代码完整性和链接目标。排序 agent 采用独立、无历史的模型请求，不提供网页工具，也不传检索元数据。题面或源码本身可能暗示原题身份，模型已有知识也无法完全消除，因此“盲排”是输入隔离而非绝对无先验。

`BLIND_RANK_ROUNDS` 控制盲排轮数（1–5，默认 3）。更多轮数有助于发现排序不稳定，但会线性增加模型调用成本。算法对参考题 rating 采用“总体难度参照”而不是要求每一对不同 rating 的题在真实难度上严格有序；报告会披露模型与 rating 梯度的分歧。

## 命令行

```bash
.venv/bin/xcpc-problemjudger problem.pdf std.cpp --references 12 --output report.json
```

## HTTP API

网页公开访问，不要求账号或口令。上传 PDF 不得超过 10 页、20 MB，std 不得超过 1 MB。同一来源 IP 在滑动的 10 分钟窗口中最多提交 5 次 PDF；`/jobs` 与 `/evaluate` 共用配额，超额返回 HTTP 429 和 `Retry-After`。无效提交也计入配额，以免反复解析恶意文件。

```bash
deploy/run-sandbox.sh
curl -H 'X-XCPC-ProblemJudger-Request: 1' \
  -X POST http://127.0.0.1:8765/evaluate \
  -F statement_pdf=@problem.pdf -F std_file=@std.cpp -F reference_count=12
```

打开 `http://127.0.0.1:8765/` 可使用中文上传页面，查看进度与报告。任务 ID 保存在当前浏览器标签的 sessionStorage，刷新后会恢复进度；关闭标签也不会取消服务器上的评测。`/history` 同时展示排队和评测中的任务，以及**启用历史库后**完成的评测。进行中任务的详情页显示阶段进度；完成记录可看完整评分详情，列表可分页加载，报告保存在 SQLite 中，重启后仍可查看。历史库只保存评分报告与摘要，不保存上传的 PDF、std 文件或参考题源码。进行中任务仅存在服务进程的内存中，服务重启会中断并清除。前端使用 `POST /jobs` 创建异步任务，轮询 `GET /jobs/{job_id}`；`POST /evaluate` 保留同步 JSON 接口。公开只读接口为 `GET /api/jobs`、`GET /api/history` 与 `GET /api/history/{id}`。所有写操作须附 `X-XCPC-ProblemJudger-Request: 1`，并通过同源检查。`/docs` 与 `/openapi.json` 在公网版本关闭。临时任务状态仅在内存中保留至完成后一小时；最多同时运行或排队 4 个任务、最多保存 100 条临时任务状态。

## Cloudflare Tunnel 公网发布与隔离

1. Tunnel 公网主机名指向 `http://127.0.0.1:8765`；无需配置 Cloudflare Access 登录。可另外在 Cloudflare 边缘启用 WAF/限流来减轻分布式请求压力。
2. 例如在自己的部署中设置 `AUTOJUDGER_PUBLIC_HOST=judge.example.org`。未配置时应用只接受 `localhost`、`127.0.0.1`、`::1` 的 Host。公网 Host 必须逐字列入允许名单；不要用通配符。保持应用只监听 `127.0.0.1`，不要开放机器的 8765 入站端口。
3. 推荐用容器运行，确保进程不能读取宿主机文件。`Dockerfile` 只复制程序文件；下列命令将容器根文件系统设为只读，只把独立的历史目录挂载为可写，给 `/tmp` 临时空间，移除 Linux capabilities，并只把服务端口发布到宿主机回环地址。先建立宿主机历史目录并赋予容器用户 UID 65532 写权限。环境文件保存在仓库外，权限设为 `0600`，至少包含 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`、`AUTOJUDGER_PUBLIC_HOST`。如需离线语料，只单独挂载已审核的 JSON 文件为只读，不要挂载用户家目录或 Docker socket。

```bash
docker build -t xcpc-problemjudger:secure .
docker run -d --name xcpc-problemjudger --restart unless-stopped --init \
  --read-only --tmpfs /tmp:rw,nosuid,noexec,size=128m,mode=1777 \
  --cap-drop ALL --security-opt no-new-privileges \
  --pids-limit 128 --memory 1g --cpus 2 \
  --env-file /path/outside/repository/xcpc-problemjudger.env \
  -e AUTOJUDGER_HISTORY_DB=/app/data/history.sqlite3 \
  --mount type=bind,src=/path/to/private/history,dst=/app/data \
  -p 127.0.0.1:8765:8000 xcpc-problemjudger:secure
```

当前机器也提供 `deploy/run-sandbox.sh`，用 Bubblewrap 只读挂载 Python 运行时、虚拟环境和 `src`，只给项目的 `data/` 历史目录写权限，隐藏工作区其他目录与用户家目录，并将 `/tmp` 放进独立临时文件系统。它仍共享宿主机网络；需要更强的网络隔离时使用容器并另设出站防火墙，只允许模型端点、Codeforces 和经审核的源码站点。任何公网服务都无法保证绝无漏洞；上线前还需更新依赖与宿主机补丁。

服务在读取上传文件前先验证 Host、来源与 IP 配额，并将整个 HTTP 请求限制为 22 MiB；对超额和并发请求拒绝处理。Cloudflare Tunnel 的请求仅在**本机连接且 Host 匹配公网主机名**时信任 `CF-Connecting-IP`，其他情况使用直接连接 IP；忽略客户端提供的 `X-Forwarded-For`，并关闭 Uvicorn 对代理头的自动信任。限流计数保存在单进程内存，服务重启会清空；若部署多个实例，须改用共享计数存储或 Cloudflare 边缘限流。页面不用外部字体、脚本或图片；题解源码跳转不会自动跟随，防止从允许的网站跳转到内网地址。详细内部异常只写服务日志，不返回给访问者。上传的题面和 std 仍会按业务需要发往配置的模型服务；请确认该模型服务的数据处理方式符合你的要求。

## 参考题获取与离线语料

实时模式使用 Codeforces 的 `problemset.problems` 获取标签和 rating，从题页的官方 Tutorial 链接定位博客，再用 `blogEntry.view` 读取出题人题解，并按题号截取解法。它是官方题解展示的实现，不等于每题都有可下载的官方 std 文件。提取 agent 可处理非标准 HTML、内嵌代码和题解中的源码链接；链接仅允许当前比赛的 Codeforces 提交页或明确指向源码文件的 GitHub 链接。访问不到或只有片段时跳过该题。

实际抽查发现多种格式：`1234A` 使用完整嵌入代码块；`1990A` 使用标题分节；`1730A` 只给出判题提交链接；`2032A` 给出题目代码片段和另一个通用模板。前三种情形分别尝试直接提取、按题号定位、读取链接源码；`2032A` 的模板与题目片段可组合为完整程序，已通过 C++ 语法检查。无法获取可核验完整源码的题目会跳过（当前环境中 `1730A` 的提交页无法读取）。Codeforces 访问限制或页面改版仍可能影响其他题；服务不会绕过站点访问限制。

可以由服务管理员设置 `REFERENCE_MANIFEST=/absolute/path/references.json` 使用已审核的离线参考语料。格式为 JSON 数组，每项如下：

```json
{
  "contestId": 4,
  "index": "A",
  "name": "Watermelon",
  "rating": 800,
  "tags": ["brute force", "math"],
  "statement": "The complete problem statement ...",
  "source": "// Complete code from official editorial ...",
  "editorialUrl": "https://codeforces.com/blog/entry/12345"
}
```

语料应只收录真实 Codeforces 题目、完整题面及核验过的题解代码；题面和源码的再分发须遵守原站点条款。生产使用宜由后台定期构建并人工抽检语料。参考题至少 70% 处于不同 rating、整体跨度至少 800；相邻 rating 间距通常不超过 600，覆盖范围较宽且参考题较少时按平均间距放宽，否则拒绝难度评分。

## 测试

```bash
.venv/bin/pytest -q
```

测试用假的 agent 和参考语料验证题面评分权重、std 输入隔离、盲排边界、CF rating 换算、非标准题解的逐字校验、片段拒绝、失败时不评分以及 PDF 输入检查，不调用付费模型或 Codeforces。

## 实时抽样验证

设置 `OPENAI_API_KEY`、`OPENAI_BASE_URL` 和可选的 `EXTRACTOR_MODEL` 后运行：

```bash
PYTHONPATH=src .venv/bin/python scripts/verify_codeforces_sample.py
```

脚本从 Codeforces 实际读取 10 道覆盖 CF 800–2200 的题，逐题要求可提取的题面、官方题解和完整程序。C++ 代码用 `g++ -fsyntax-only`，Python 代码用 AST 做语法检查；不会执行题解程序。审计结果保存在 `validation/codeforces-sample.json`，只含来源链接、长度、哈希、验证结果和失败原因，不保存题面、源码或 API key。2026-10-18 的实测为 20 次尝试、10 道通过；官方题解只有片段、动态占位内容、提交页被拒绝或题面图片缺少文本描述的题会跳过。

同日还用 `2032A` 的题面与官方题解代码做了 1 轮完整 pipeline 冒烟测试：当时旧版 10 分制的清晰度评为 8.9/10，实时获取的 10 道参考题覆盖 CF 800–2600，难度估计 CF 800，与该题官方 rating 一致。简要结果见 `validation/end-to-end-smoke.json`。这是历史链路验证，不代表新版 5 分制的评分结果或统计精度。
