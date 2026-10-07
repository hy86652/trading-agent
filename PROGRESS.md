# 项目进度与上下文记录

> 本文件是项目的**运行日志**，用来在每次新会话开始时快速恢复上下文。
> 项目的需求与约定以 `project_brief.md` 为准（位于 `D:\github\tradeing_agent\project_brief.md`）。
>
> **约定**：每次开始工作前先读本文件和 `project_brief.md`；每完成一个阶段或做出一个重要决策后，回来更新本文件。

---

## 1. 当前状态（最后更新：2026-10-07）

- **环境搭建**：已完成
- **阶段 0（数据与账户准备）**：已完成并通过自检，用户已确认
- **阶段 1（Task 1–3 最小 Agent）**：已完成并通过用户确认（真实 Gemini 测试：AAPL MA5 = 184.68，与 CSV 一致；离线图检查通过）
- **阶段 2（Task 4，MCP）**：已完成并通过用户确认（真实 Gemini 测试 MA5 = 184.68）
- **阶段 3（Task 5–6 钩子与 HITL）**：Pre-hook（400 校验、20% 上限、连续 3 次失败停止）与人工审批已完成，用户测试通过。Post-tool Hook 已实现（用户确认三项：成交摘要、截断、日志），离线检查通过；**真实模型下的 Post-hook 效果待用户测试**
- **阶段 5 评估（Task 9 Eval）**：代码已完成，离线检查通过（`python -m scripts.check_eval`）。`eval/record.py` 用 `eval/cases.json` 中的 10 个用例（含故意出错）录制 trace 到 `eval/traces/`；`eval/judge.py` 用 LLM-judge 打 1–5 分，结果写入 `eval/results.json`。**两者都需要真实模型调用，尚未运行**
- **README**：`README.md` 已写好（安装、运行、架构、已知风险）
- **brief 第 12 节与本节**：已于 2026-10-07 同步，阶段 3–6 均未勾选，等待真实测试与确认
- **阶段 6 部署（Task 9 Deploy）**：`api/main.py` 已完成（`POST /webhook`、`POST /approve`、`GET /health`），离线检查通过（`python -m scripts.check_api`）。`Dockerfile` 与 `.dockerignore` 已写好，**未构建**（本机没有 Docker）。部署到 Render / HF Spaces 尚未做
- **Langfuse 配置问题**：`.env` 中的密钥有效，但 `LANGFUSE_HOST` 为 `https://cloud.langfuse.com`，服务器返回 401。同一对密钥在 `https://us.cloud.langfuse.com` 上认证通过。需要用户把 `.env` 中的 `LANGFUSE_HOST` 改为美国区地址。离线检查已强制关闭追踪，不会发送 trace
- **阶段 5（Task 8 观测）**：Langfuse 核心 SDK 的 `@observe` / `start_as_current_observation` 已接入（不装 `langchain` 包）。每个问题一个 trace，LLM 调用为 generation（模型名、token 用量），MCP `tools/call` 为 tool span（JSON-RPC 请求与响应）。**没有密钥时自动禁用**；**需要用户填写密钥后，在 Langfuse 界面中验证结构**。评估脚本见上文阶段 5 评估
- **阶段 4（Task 7 压缩与检查点）**：代码已完成，离线检查通过（压缩切分不拆分工具调用对、长对话实际压缩、重启后同一 thread 恢复）；`cli.py` 启动与恢复路径已验证；**真实模型的多轮与重启测试待做**
- **账户状态**：`data/portfolio.json` 当前为现金 99819.36、AAPL 1 股（阶段 3 批准测试的结果，已由用户确认保留）

---

## 2. 环境信息

- 项目路径：`D:\github\trading-agent`
- Python：3.12.13（由 `uv` 下载安装）。系统默认的 `python` 是 3.14.5，**不要用**
- 虚拟环境：`D:\github\trading-agent\.venv`，由 `python -m venv` 创建（自带 pip）
- 激活（PowerShell）：`.\.venv\Scripts\Activate.ps1`。尚未测试该命令，如遇执行策略报错，需用户决定是否修改策略
- 安装依赖时使用：`.\.venv\Scripts\python.exe -m pip install ...`
- Git：分支 `main`，远程 origin 为 `https://github.com/hy86652/trading-agent`（私有仓库，账户 hy86652）
- 已提交并推送：`fe5829f`（初始提交，阶段 0 相关文件）
- 推送方式：`gh` 已安装并登录（账户 hy86652）。PowerShell 中若找不到 `gh`，需先刷新 PATH
- Docker：未安装（部署阶段需要）

---

## 3. 文件清单

| 路径 | 说明 | 状态 |
|---|---|---|
| `.env` | 密钥模板，值为空，由用户填写 | 已创建，**不读取、不打印** |
| `.gitignore` | 忽略 `.venv/`、`.env`、`__pycache__/`、`*.db` | 已创建 |
| `requirements.txt` | `pip freeze` 输出，91 个包 | 已生成 |
| `scripts/gen_data.py` | 生成 5 个 ticker 的模拟行情 CSV | 已完成 |
| `data/prices/*.csv` | AAPL、MSFT、NVDA、GOOGL、AMZN，各 100 行 | 已生成 |
| `data/portfolio.json` | 模拟账户，初始现金 100,000，无持仓 | 已创建 |
| `tools.py` | 4 个工具的普通 Python 版本，内置 `_self_test` | 已完成，自检通过 |
| `agent/state.py` | AgentState（Task 1），字段与 brief 第 5 节一致 | 已完成 |
| `agent/nodes.py` | reasoner（只决策）与 tool_exec（路由到 tools.py）、System Prompt | 已完成 |
| `agent/graph.py` | reasoner ⇄ tool_exec 循环，工具来自 MCP Client | 已完成 |
| `mcp_server/server.py` | MCP Server（stdio），注册全部 4 个工具 | 阶段 2 新增，离线通过 |
| `agent/mcp_client.py` | MCP Client：启动 Server，`tools/list` 发现工具，`tools/call` 调用；后台线程桥接同步节点 | 阶段 2 新增，离线通过 |
| `cli.py` | 终端入口，会话期间保持 MCP Server 运行；`build_llm()` 为唯一需改动的模型配置点 | 已完成，真实测试待配额恢复 |
| `scripts/check_graph.py` | 用脚本化假 LLM 检查循环（启动真实 MCP Server，无需 key），运行：`python -m scripts.check_graph` | 已完成，通过 |
| `PROGRESS.md` | 本文件 | — |

---

## 4. 关键决策记录

| 日期 | 决策 | 原因 |
|---|---|---|
| 2026-10-05 | 项目放在 `D:\github\trading-agent`，不放在 `tradeing_agent` 内 | 避免嵌套 git 仓库；用户确认 |
| 2026-10-05 | 使用 Python 3.12，不用系统默认的 3.14 | 项目要求 3.11/3.12，3.13 及以上不可用；用户确认 |
| 2026-10-05 | 虚拟环境用 `python -m venv` 创建 | 自带 pip，便于按要求用 pip 安装 |
| 2026-10-05 | 依赖按清单安装，`requirements.txt` 由 `pip freeze` 生成 | 用户要求；安装无冲突 |
| 2026-10-05 | 行情为工作日（`bdate_range`），不排除美股节假日 | 模拟数据，节假日对练习无影响，属于简化 |
| 2026-10-05 | 行情使用固定种子 42 与起始日 2026-01-02 | 保证重复生成结果一致 |
| 2026-10-05 | ticker 白名单 = `data/prices/` 下的 CSV 文件名 | 防止路径穿越，且不需要额外配置 |
| 2026-10-05 | 指标参数用字符串表示，如 `"MA5"`、`"RSI14"`；RSI 使用 Wilder 平滑 | 便于 LLM 直接传参；Wilder 是常用约定 |
| 2026-10-05 | 工具错误统一返回 `{"error": "..."}` | 调用方只需检查一个键 |
| 2026-10-05 | 阶段 0 的 `place_order` 立即成交，只做基本检查 | pre-hook 与 HITL 属于阶段 3，按计划分阶段实现 |
| 2026-10-05 | 目录结构沿用 brief 第 8 节，但暂未创建 `mcp_server/`、`api/`、`eval/` | 按阶段逐步创建，不提前建空目录 |
| 2026-10-05 | 阶段 1 只向 LLM 绑定 3 个只读工具，**不绑定 `place_order`** | 下单需先经过 pre-hook（Task 5）和 HITL（Task 6），提前开放有风险 |
| 2026-10-05 | 工具函数直接用 `StructuredTool.from_function` 包装，不改 `tools.py` | 保持阶段 0 的函数签名稳定，MCP 迁移时更容易替换 |
| 2026-10-05 | 工具执行错误（未知工具、参数错误）作为数据返回给 LLM，不抛异常 | 让 reasoner 能根据错误自我纠正（brief 第 4 节要求） |
| 2026-10-05 | System Prompt 在每次调用时拼接，不写入 messages | 避免历史中重复累积 |
| 2026-10-05 | LLM 默认模型名 `llama-3.3-70b-versatile`，**未核实当前 Groq 模型目录** | 提供方尚未确定；如选 Gemini 需改 `build_llm()` |
| 2026-10-05 | 阶段 1 每个问题是独立运行，不保留对话历史 | 记忆与 checkpoint 属于阶段 4（Task 7） |
| 2026-10-05 | LLM 提供方选定 Gemini，使用 `langchain-google-genai`，key 读取 `GOOGLE_API_KEY` | 用户选择；Groq 不再使用 |
| 2026-10-05 | 阶段 2：Server 注册全部 4 个工具，Agent 只绑定 3 个只读工具（`READ_ONLY_TOOL_NAMES`），`tool_exec` 同样拒绝未绑定的工具 | 保留 place_order 的安全约束；阶段 3 在 Agent 端放开并加 pre-hook 与审批（用户确认） |
| 2026-10-05 | MCP Client 在后台线程的事件循环中持有会话，对外提供同步的 `call()` | LangGraph 节点为同步函数，MCP SDK 为异步；SDK 的 stdio 传输要求进出 task group 在同一个 task 中完成 |
| 2026-10-06 | 阶段 4：超过 10 条消息时压缩最旧的 8 条，切分点后移以免拆散工具调用与结果；压缩在每个问题开始、工具执行后、拒绝订单后检查 | brief 第 6 节；孤立的 tool 结果会导致模型 API 报错 |
| 2026-10-07 | 阶段 6 审批方案：`/webhook` 遇到订单返回 `pending_approval`，`/approve` 完成审批（brief 第 10 节的第一个选项）；同一线程有待审批订单时 `/webhook` 返回 409 | 线上没有终端审批；用户未明确选择，按推荐方案实现，可改 |
| 2026-10-07 | 没有 Langfuse 密钥时，或在离线检查中，关闭追踪（`LANGFUSE_TRACING_ENABLED=false`） | 密钥为空时 SDK 持续上报 401；离线检查不应发送 trace |
| 2026-10-07 | Post-tool Hook 三项：成交摘要（place_order filled）、超过 4000 字符截断、每次工具调用写入 `data/logs/tool_calls.jsonl`（已 gitignore） | 用户确认；截断阈值先设大，因为当前输出很短 |
| 2026-10-07 | Langfuse 采用核心 SDK（`observe`、`start_as_current_observation`），不装 `langchain` 包 | 用户选择方案 B；brief 禁止未提及的重量级依赖 |
| 2026-10-07 | `cli.py` 在单个问题失败时打印简短错误并保持会话；配额错误不再退出程序 | 配额错误曾导致退出，问题被当成 shell 命令 |
| 2026-10-06 | 阶段 4：一个会话 = 一个 thread，历史跨问题保留；`cli.py --thread <id>` 恢复会话；每个问题重置 pending_order、approval、error_count | 满足"同一 thread id 从上次状态继续"；error_count 不重置会导致恢复后误触发停止 |
| 2026-10-05 | Server 的 `place_order` 在 Server 端仍可调用，但 Agent 不绑定；Server 的 stdout 只用于协议，不打印 | stdio 传输占用 stdout；安全由 Agent 端控制 |
| 2026-10-05 | 模型名从 `gemini-2.5-flash` 改为 `gemini-3.8-flash` | 2.5-flash 对新用户返回 404，错误信息建议 3.8-flash；3.8-flash 测试通过 |
| 2026-10-05 | `cli.py` 增加 `to_text()`，只取 Gemini 返回内容块中的文本 | Gemini 的 content 可能是列表，直接打印会显示原始结构 |

---

## 5. 待办与待确认事项

- [ ] **用户确认阶段 0 的结果**（确认前不进入阶段 1）
- [ ] **LLM 提供方**：Groq 还是 Gemini，由用户在 `.env` 中决定。两个包都已安装，但尚未选定。**阻塞阶段 1 的真实测试**
- [ ] **Groq 模型名核实**：`llama-3.3-70b-versatile` 未在线核实，首次真实运行时若报模型不存在，需改为当前可用模型
- [ ] **Post-tool Hook 具体做哪几项**：brief 第 4 节要求实现前与用户确认
- [x] **首次 git 提交与远程仓库**：已提交 `fe5829f` 并推送到私有仓库。`.env` 未被提交
- [ ] **后续提交策略**：每个阶段结束后是否提交、推送，由用户决定
- [ ] **Docker**：部署阶段（阶段 6）前需安装
- [ ] **`PROJECT.md` 与 `project_brief.md`**：brief 第 8 节写的是 `PROJECT.md`，实际文件名是 `project_brief.md`。目前以 `project_brief.md` 为准
- [ ] **项目位置**：brief 与项目分属两个目录。是否需要统一，待用户决定

---

## 6. 工作约定（摘自 brief，保持提醒）

- 严格按阶段顺序推进；每个阶段结束后汇报并等待确认
- 不超出范围，不新增工具或未在 brief 中提及的重量级依赖
- 不读取、打印、猜测或提交任何 API key；密钥只来自 `.env`
- 不执行不可逆的外部操作（推送远程仓库、部署、注册账号等），需先请用户确认
- 遇到需要决策的地方先停下来问用户

---

## 7. 会话日志

- **2026-10-05**
  - 完成环境搭建：Python 3.12 虚拟环境、依赖安装、`.env` / `.gitignore` / `requirements.txt`、`git init`
  - 完成阶段 0：数据生成脚本、模拟账户、`tools.py` 及自检（全部通过）
  - 在 `project_brief.md` 中勾选"环境搭建"
  - 新建本文件 `PROGRESS.md`
  - 安装 `gh`（2.102.0），用户完成登录后，创建私有仓库 `hy86652/trading-agent` 并推送 `main`（`fe5829f`）
  - 用户确认阶段 0，开始阶段 1：完成 `agent/`（state、nodes、graph）、`cli.py`、`scripts/check_graph.py`；离线检查通过；`ChatGroq.bind_tools` 接受工具 schema。真实 LLM 测试待提供方与 key
  - 用户选择 Gemini 并填写 `GOOGLE_API_KEY`。首次真实运行：`gemini-2.5-flash` 返回 404，改为 `gemini-3.8-flash`；一次 503（高负载）后重试成功
  - 真实提问"AAPL 最近 5 日的 MA5"返回 184.68，与 CSV 计算结果一致。修复 `cli.py` 输出内容块列表的问题。用户确认阶段 1 完成。同步更新 `project_brief.md` 第 12 节：阶段 0、阶段 1 勾选。Gemini 免费档每日 20 次请求，本次测试中配额曾用完，等待恢复
  - 开始阶段 2（MCP）：新增 `mcp_server/server.py`、`agent/mcp_client.py`；`nodes.py` 去掉硬编码工具字典；`graph.py`、`cli.py`、`scripts/check_graph.py` 改为通过 MCP Client 获取工具。离线检查通过；`bind_tools` 对 MCP 生成的 schema 转换正常；`cli.py` 启动与退出正常。真实 Gemini 测试待配额恢复
