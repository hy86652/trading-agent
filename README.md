# 模拟交易助手 Agent

一个用 LangGraph 搭建的模拟交易助手。Agent 通过 MCP 调用行情和账户工具，下单前经过确定性校验，并需要人工审批。

> **只用于模拟。** 所有行情是生成的模拟数据，账户是本地 JSON 文件。项目不连接任何真实券商或资金账户。

## 功能

- 查询模拟行情（最近收盘价）、计算指标（MA、RSI）、查看模拟账户
- 模拟下单：下单前检查 ticker、方向、数量、现金或持仓、单笔不超过总资产的 20%
- 下单需要人工批准（终端 `approve` / `reject`，或 API 的 `/approve`）
- 上下文超过 10 条消息时压缩最旧的 8 条；会话状态保存在 SQLite，可以用会话 ID 恢复
- 工具调用日志写入 `data/logs/tool_calls.jsonl`；可选接入 Langfuse 追踪

## 架构

```
START → [summarize] → reasoner ─┬─(文字回答)──────────────→ END
                                └─(工具调用)→ pre_hook ─┬─(校验失败 400)→ reasoner
                                                        ├─(下单)→ human_approval ─┬─(approve)→ tool_exec
                                                        │                         └─(reject)→ reasoner
                                                        └─(只读工具)────────────→ tool_exec
tool_exec → post_hook → [summarize] → reasoner
```

- **reasoner**：只决定下一步，不执行工具。
- **pre_hook**：确定性规则（`agent/hooks.py`）。失败时返回模拟的 400 错误，LLM 可以据此修正。连续 3 次失败则停止本轮。
- **human_approval**：用 LangGraph 的 `interrupt` 暂停，状态存入 checkpointer。
- **tool_exec**：通过 MCP 客户端调用工具（`agent/mcp_client.py` → `mcp_server/server.py`）。
- **post_hook**：为成交的订单加一条摘要，截断过长输出，写日志。
- **summarize**：独立的 LLM 调用，压缩旧消息。

## 目录

```
agent/        图、节点、钩子、MCP 客户端、观测配置
mcp_server/   MCP Server，注册 4 个工具
api/          FastAPI 接口
eval/         录制 trace（record.py）与 LLM-judge 评估（judge.py）
scripts/      离线检查脚本、生成模拟行情
data/         模拟行情（CSV）、模拟账户（portfolio.json）、检查点与日志（不提交）
cli.py        终端入口
tools.py      4 个工具的 Python 实现，MCP Server 调用它们
```

## 安装

需要 Python 3.12（不要用 3.13）。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`requirements.txt` 是 Windows 上的 `pip freeze`，包含仅 Windows 可用的 `pywin32`。Linux 环境请去掉这一行，Dockerfile 已经这样处理。

生成模拟行情（仓库已包含，只在需要重新生成时运行）：

```powershell
.\.venv\Scripts\python.exe -m scripts.gen_data
```

## 配置

在项目根目录创建 `.env`（不要提交）：

| 变量 | 必需 | 说明 |
|---|---|---|
| `GOOGLE_API_KEY` | 是 | Gemini API key |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | 否 | 不填则关闭追踪 |
| `LANGFUSE_HOST` | 否 | 密钥所在区域的地址，例如 `https://us.cloud.langfuse.com` |

`GROQ_API_KEY` 在 `.env` 模板中，但当前代码未使用。模型名在 `cli.py` 的 `GEMINI_MODEL` 中修改。

## 运行

**离线检查**（不需要 API key，不消耗配额）：

```powershell
.\.venv\Scripts\python.exe -m scripts.check_graph
.\.venv\Scripts\python.exe -m scripts.check_eval
.\.venv\Scripts\python.exe -m scripts.check_api
```

这些检查会启动真实的 MCP Server，并在结束时还原模拟账户。

**终端**：

```powershell
.\.venv\Scripts\python.exe cli.py                 # 新会话，启动时打印会话 ID
.\.venv\Scripts\python.exe cli.py --thread <ID>   # 恢复会话
```

**API**：

```powershell
.\.venv\Scripts\python.exe -m uvicorn api.main:app --port 8000
```

```powershell
# 提问；遇到订单会返回 pending_approval
curl -X POST localhost:8000/webhook -H "Content-Type: application/json" -d '{"question": "买 1 股 AAPL"}'
# 审批（用上一步返回的 thread_id）
curl -X POST localhost:8000/approve -H "Content-Type: application/json" -d '{"thread_id": "<ID>", "decision": "reject"}'
```

**评估**（会调用真实模型，消耗配额）：

```powershell
.\.venv\Scripts\python.exe -m eval.record   # 录制 eval/cases.json 中的用例到 eval/traces/
.\.venv\Scripts\python.exe -m eval.judge    # 评分，结果写入 eval/results.json
```

**容器**（尚未构建验证）：

```bash
docker build -t trading-agent .
docker run -p 7860:7860 -e GOOGLE_API_KEY=... trading-agent
```

## 安全边界

- 只使用模拟数据和模拟账户，不接入任何真实交易接口。
- 不读取、打印、提交 API key。密钥只从 `.env` 或托管平台的环境变量读取。
- `place_order` 只有通过 pre-hook 且经人工批准后才会执行。
- 大模型只能通过工具获得价格和持仓。摘要和回答中的数值应以工具返回为准。

## 已知风险

1. **线上审批**：终端审批只适用于本地。线上通过 `/webhook` 和 `/approve` 两步完成审批。拿到 `thread_id` 的人都可以审批该线程，因此线上不应对公网开放下单能力，或者应加上鉴权（目前没有）。
2. **检查点持久性**：检查点保存在 SQLite 文件中。Render、HF Spaces 等平台的磁盘在容器重启后可能丢失，会话会随之丢失。
3. **模型幻觉**：模型可能编造价格或指标。系统提示要求数值来自工具，评估中会重点观察，但不能完全避免。
4. **免费档限流**：Gemini 免费档每天 20 次请求。录制和评估会很快耗尽配额，超出后请求会失败，直到次日重置。
5. **追踪数据**：开启 Langfuse 后，提问内容和工具结果会发送到 Langfuse。不要在提问中输入真实的个人或账户信息。

## 阶段进度

见 `PROGRESS.md`。
