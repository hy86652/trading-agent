"""Read question traces back from Langfuse and turn them into the transcript format the judge uses (brief section 9).

Each question is one Langfuse trace (see cli.py). Its observations hold the model calls, the MCP tool calls,
pre-hook rejections and human decisions. This module rebuilds the dialogue from them, so the judge sees the
same conversation whether it came from eval/record.py or from production traces.

The legacy `/api/public/traces` endpoint is not available to organizations created after 2026-09-16, so this
reads observations through the v2 observations endpoint instead. Input and output come back as raw strings.

Run from the project root with: python -m eval.judge --source langfuse --limit 10
"""
import json
from types import SimpleNamespace
from typing import Any

from agent.observability import tracer, tracing_enabled

QUESTION_SPAN = "question"
REASONER_SPAN = "llm_reasoner"  # summarize calls are compression, not part of the dialogue, so they are skipped
TOOL_PREFIX = "mcp tools/call "
PRE_HOOK_SPAN = "pre_hook_check"
DECISION_SPAN = "human_decision"
FIELDS = "core,basic,time,io"  # the groups that include name, start time, input and output


def _io(value: Any) -> Any:
    """Input and output arrive as raw strings. Parse JSON objects and arrays; leave plain text alone."""
    if isinstance(value, str) and value.lstrip()[:1] in ("{", "["):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _time_key(observation: Any) -> float:
    start = observation.start_time
    return start.timestamp() if hasattr(start, "timestamp") else (start or 0)


def transcript_from_observations(question: str, observations: list, answer: str | None) -> list[dict[str, Any]]:
    """Rebuild the dialogue from the observations of one question trace, in the order they happened."""
    rows: list[dict[str, Any]] = [{"role": "human", "content": question}]
    for obs in sorted(observations, key=_time_key):
        name = obs.name or ""
        output = _io(obs.output)
        output = output if isinstance(output, dict) else {}
        input_ = _io(obs.input)
        input_ = input_ if isinstance(input_, dict) else {}
        if name == REASONER_SPAN:
            row: dict[str, Any] = {"role": "ai", "content": output.get("content", "")}
            if output.get("tool_calls"):
                row["tool_calls"] = output["tool_calls"]
            rows.append(row)
        elif name.startswith(TOOL_PREFIX):
            params = input_.get("params", {})
            rows.append({
                "role": "tool",
                "tool": params.get("name", name[len(TOOL_PREFIX):]),
                "content": json.dumps(output.get("result", {}), ensure_ascii=False),
            })
        elif name == PRE_HOOK_SPAN:
            # The pre-hook answers every call in the turn with a 400, so one result per requested call.
            payload = json.dumps(output, ensure_ascii=False)
            for call_name in input_.get("calls", []):
                rows.append({"role": "tool", "tool": call_name, "content": payload})
        elif name == DECISION_SPAN and output.get("decision") == "reject":
            rows.append({
                "role": "tool",
                "tool": "place_order",
                "content": json.dumps({"status": "rejected", "error": "the user rejected this order"}),
            })
    # The final answer is normally the last reasoner output; add it only if the trace has no plain answer last.
    if answer and (rows[-1]["role"] != "ai" or rows[-1].get("tool_calls")):
        rows.append({"role": "ai", "content": answer})
    return rows


def trace_from_langfuse(detail: Any) -> dict[str, Any]:
    """Convert one Langfuse trace (its id and all of its observations) into the judge's trace format."""
    observations = list(detail.observations or [])
    root = next((o for o in observations if o.name == QUESTION_SPAN), None)
    question = (root.input if root else None) or ""
    answer = _io(root.output if root else None)
    if isinstance(answer, dict):
        answer = answer.get("content")
    return {
        "id": detail.id,
        "question": str(question),
        "transcript": transcript_from_observations(str(question), observations, answer),
    }


def _observations(client: Any, **filters: Any) -> list:
    """Every page of observations matching the filters."""
    collected, cursor = [], None
    while True:
        page = client.api.observations.get_many(fields=FIELDS, limit=100, cursor=cursor, **filters)
        collected.extend(page.data)
        cursor = page.meta.cursor if page.meta else None
        if not cursor:
            return collected


def fetch_question_traces(limit: int) -> list[dict[str, Any]]:
    """The most recent question traces, converted. Needs Langfuse keys and the correct LANGFUSE_HOST."""
    if not tracing_enabled():
        raise RuntimeError("Langfuse is not configured: set LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_HOST in .env")
    client = tracer()
    roots = client.api.observations.get_many(
        fields=FIELDS, limit=limit, name=QUESTION_SPAN, is_root_observation=True
    ).data
    traces = []
    for root in roots[:limit]:
        children = _observations(client, trace_id=root.trace_id)
        traces.append(trace_from_langfuse(SimpleNamespace(id=root.trace_id, observations=children)))
    return traces


def example_detail() -> SimpleNamespace:
    """A small hand-built trace in Langfuse's shape: an order the user rejects. Used by the offline checks."""
    return SimpleNamespace(
        id="example",
        observations=[
            SimpleNamespace(name=QUESTION_SPAN, start_time=0, input="Buy 1 share of AAPL", output="Order cancelled."),
            SimpleNamespace(name=REASONER_SPAN, start_time=1, input=None, output={"content": "", "tool_calls": [
                {"name": "place_order", "args": {"ticker": "AAPL", "side": "buy", "quantity": 1}},
            ]}),
            SimpleNamespace(name=DECISION_SPAN, start_time=2, input={}, output={"decision": "reject"}),
            SimpleNamespace(name=REASONER_SPAN, start_time=3, input=None, output={"content": "Order cancelled.", "tool_calls": []}),
        ],
    )
