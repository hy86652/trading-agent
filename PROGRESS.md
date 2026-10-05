# 项目进度与上下文记录

> 本文件是项目的**运行日志**，用来在每次新会话开始时快速恢复上下文。
> 项目的需求与约定以 `project_brief.md` 为准（位于 `D:\github\tradeing_agent\project_brief.md`）。
>
> **约定**：每次开始工作前先读本文件和 `project_brief.md`；每完成一个阶段或做出一个重要决策后，回来更新本文件。

---

## 1. 当前状态（最后更新：2026-10-05）

- **环境搭建**：已完成
- **阶段 0（数据与账户准备）**：已完成并通过自检，用户已确认
- **阶段 1（Task 1–3 最小 Agent）**：代码已完成，离线图检查通过；**真实 LLM 端到端测试待完成**
  - 阻塞项：LLM 提供方未定（Groq 或 Gemini），且 `.env` 中还没有 key
- **下一步**：用户选定提供方并填好 `.env` 后，运行 `python cli.py` 做一次真实提问，确认后进入阶段 2

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
| `agent/graph.py` | reasoner ⇄ tool_exec 最小循环 | 已完成 |
| `cli.py` | 终端入口，每个问题独立运行，`build_llm()` 为唯一需改动的模型配置点 | 已完成，待真实测试 |
| `scripts/check_graph.py` | 用脚本化假 LLM 检查循环（无需 key），运行：`python -m scripts.check_graph` | 已完成，通过 |
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
