# Simulated Trading Assistant Agent

A simulated trading assistant built with LangGraph. The agent calls market and account tools over MCP, checks every order with deterministic rules before it is sent, and requires human approval to trade.

> **Simulation only.** All market data is generated mock data, and the account is a local JSON file. The project does not connect to any real broker or funds account.

## Features

- Simulated market data (latest close), indicators (MA, RSI) and account view
- Simulated orders, checked before approval: ticker, side, quantity, cash or position, and at most 20% of total assets per order
- Human approval for every order (`approve` / `reject` in the terminal, or `/approve` over the API)
- When the history exceeds 10 messages, the oldest 8 are compressed. Session state is stored in SQLite and can be resumed with a session ID
- Tool calls are logged to `data/logs/tool_calls.jsonl`. Optional Langfuse tracing

## Architecture

```
START → [summarize] → reasoner ─┬─(text answer)────────────→ END
                                └─(tool calls)→ pre_hook ─┬─(check failed, 400)→ reasoner
                                                          ├─(place_order)→ human_approval ─┬─(approve)→ tool_exec
                                                          │                                └─(reject)→ reasoner
                                                          └─(read-only tools)─────────────→ tool_exec
tool_exec → post_hook → [summarize] → reasoner
```

- **reasoner**: decides the next step only. It never runs a tool.
- **pre_hook**: deterministic rules (`agent/hooks.py`). A failure returns a simulated HTTP 400 that the LLM can use to correct itself. Three consecutive failures stop the turn.
- **human_approval**: pauses with LangGraph's `interrupt`. The state is saved in the checkpointer.
- **tool_exec**: calls tools through the MCP client (`agent/mcp_client.py` → `mcp_server/server.py`).
- **post_hook**: adds a summary to a filled order, truncates very long outputs, and writes the log.
- **summarize**: a separate LLM call that compresses old messages.

## Layout

```
agent/        graph, nodes, hooks, MCP client, observability setup
mcp_server/   MCP server that exposes the 4 tools
api/          FastAPI application
eval/         trace recording (record.py) and LLM-as-a-judge scoring (judge.py)
scripts/      offline checks and mock market data generation
data/         mock prices (CSV), mock account (portfolio.json), checkpoints and logs (not committed)
cli.py        terminal entry point
tools.py      Python implementations of the 4 tools; the MCP server calls them
```

## Setup

Python 3.12 is required (not 3.13).

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`requirements.txt` is a `pip freeze` from Windows and includes `pywin32`, which only installs on Windows. On Linux, remove that line. The Dockerfile already does this.

To regenerate the mock prices (already in the repo; only needed if you want new data):

```powershell
.\.venv\Scripts\python.exe -m scripts.gen_data
```

## Configuration

Copy `.env.example` to `.env` and fill in the values (do not commit `.env`):

```powershell
Copy-Item .env.example .env
```

| Variable | Required | Description |
|---|---|---|
| `GOOGLE_API_KEY` | Yes | Gemini API key |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | No | Leave empty to disable tracing |
| `LANGFUSE_HOST` | No | Host for the region your keys belong to, e.g. `https://us.cloud.langfuse.com` |

`GROQ_API_KEY` is in `.env.example` for reference, but the code does not use it. Change the model name with `GEMINI_MODEL` in `agent/runtime.py`.

## Running

**Offline checks** (no API key needed, no quota used):

```powershell
.\.venv\Scripts\python.exe -m scripts.check_graph
.\.venv\Scripts\python.exe -m scripts.check_eval
.\.venv\Scripts\python.exe -m scripts.check_api
```

These checks start the real MCP server, and the one that places an order restores the simulated account afterwards.

**Terminal**:

```powershell
.\.venv\Scripts\python.exe cli.py                 # new session; prints the session ID at start
.\.venv\Scripts\python.exe cli.py --thread <ID>   # resume a session
```

**API**:

```powershell
.\.venv\Scripts\python.exe -m uvicorn api.main:app --port 8000
```

```powershell
# Ask a question; an order returns pending_approval
# (add -H "X-API-Key: $API_TOKEN" when the API_TOKEN environment variable is set)
curl -X POST localhost:8000/webhook -H "Content-Type: application/json" -d '{"question": "Buy 1 share of AAPL"}'
# Approve or reject (use the thread_id from the previous response)
curl -X POST localhost:8000/approve -H "Content-Type: application/json" -d '{"thread_id": "<ID>", "decision": "reject"}'
```

**Evaluation** (calls the real model and uses quota):

```powershell
.\.venv\Scripts\python.exe -m eval.record   # record the cases in eval/cases.json to eval/traces/
.\.venv\Scripts\python.exe -m eval.judge    # score the traces; results go to eval/results.json
```

**Container**: builds on Render (see below); not built or run locally (no Docker on the development machine).

```bash
docker build -t trading-agent .
docker run -p 7860:7860 -e GOOGLE_API_KEY=... -e API_TOKEN=... trading-agent
```

**Deployment** (Render, free plan). `render.yaml` is a Render Blueprint, deployed live:

1. In Render, create a Blueprint from this GitHub repository.
2. Fill in the secrets that the Blueprint marks as manual: `GOOGLE_API_KEY`, and optionally `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY`.
3. Copy the generated `API_TOKEN` from the service's environment settings. Every request must send it in the `X-API-Key` header.
4. Check the service with `GET /health`, which needs no token. `GET /` returns a short service description and endpoint list.

On the free plan the disk is temporary: sessions and pending approvals are lost when the service restarts or redeploys.

## Safety boundaries

- Only simulated data and a simulated account are used. No real trading interface is connected.
- API keys are never read, printed or committed by the code. They come from `.env` or the hosting platform's environment variables.
- `place_order` runs only after it passes the pre-hook and is approved by a person.
- The model gets prices and positions only from tools. Numbers in summaries and answers should match tool results.

## Known risks

1. **Approval online**: terminal approval only works locally. Online, approval is two requests: `/webhook` and then `/approve`. Access is controlled by a shared token (`API_TOKEN`, sent as `X-API-Key`). It is a single shared secret, not per-user authentication: anyone with the token can approve any thread. Leave `API_TOKEN` unset only for local use.
2. **Checkpoint persistence**: checkpoints are stored in a SQLite file. On platforms such as Render or HF Spaces, the disk may be lost when the container restarts, and sessions go with it.
3. **Model hallucination**: the model may make up prices or indicators. The system prompt requires values from tools, and the evaluation watches for this, but it cannot be fully prevented.
4. **Free-tier limits**: the Gemini free tier allows 20 requests per day. Recording and evaluation will use that up quickly. Once it is exhausted, requests fail until the next day.
5. **Trace data**: with Langfuse enabled, questions and tool results are sent to Langfuse. Do not type real personal or account information into questions.
6. **Trace query delay**: a trace sent to Langfuse is not immediately queryable through the API; it took roughly one to two minutes to appear in testing. `eval/judge.py --source langfuse` run right after `cli.py` may find nothing yet. Wait and try again before assuming the trace was lost.

## Progress

See `PROGRESS.md`.
