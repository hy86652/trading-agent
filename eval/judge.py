"""LLM-as-a-judge for error recovery (Task 9). Scores recorded conversations from 1 to 5.

Input: JSON trace files in eval/traces/, one per conversation (written by eval/record.py).
Output: eval/results.json and a printed table. The judge is a separate LLM call with no tools bound.

Run from the project root with: python -m eval.judge
"""
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from agent.nodes import to_text

ROOT = Path(__file__).resolve().parent.parent
TRACE_DIR = ROOT / "eval" / "traces"
RESULTS_PATH = ROOT / "eval" / "results.json"

JUDGE_PROMPT = """你是评审员。下面是一段模拟交易助手的对话记录，其中包含工具调用和工具结果。
请评价助手的【错误恢复能力】，重点看：
- 工具返回错误（status 为 400、含 error，或 status 为 rejected）时，助手是否根据错误信息修正参数后重试；
- 无法修正或被用户拒绝时，是否如实告知用户；
- 是否避免重复提交同一个出错的请求；
- 是否没有编造价格、指标或持仓数值。
按 1 到 5 分打分：5 表示恢复得很好，1 表示很差。如果对话中没有出现错误，无法体现恢复能力，给 3 分并说明原因。
只输出 JSON，不要输出其他内容：{"score": <1 到 5 的整数>, "reason": "<一到两句中文理由>"}"""


def transcript_from_messages(messages: list) -> list[dict[str, Any]]:
    """Turn LangChain messages into plain dicts so a trace can be saved as JSON."""
    rows = []
    for message in messages:
        row: dict[str, Any] = {"role": message.type, "content": to_text(message.content)}
        if isinstance(message, AIMessage) and message.tool_calls:
            row["tool_calls"] = [{"name": c["name"], "args": c["args"]} for c in message.tool_calls]
        if isinstance(message, ToolMessage):
            row["tool"] = message.name
        rows.append(row)
    return rows


def load_traces(directory: Path = TRACE_DIR) -> list[dict[str, Any]]:
    """Every trace file in the directory, sorted by file name."""
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*.json"))]


def render_trace(trace: dict[str, Any]) -> str:
    """The conversation as plain text for the judge."""
    lines = [f"用户问题：{trace['question']}"]
    for row in trace["transcript"]:
        if row["role"] == "ai" and row.get("tool_calls"):
            calls = ", ".join(f"{c['name']}({json.dumps(c['args'], ensure_ascii=False)})" for c in row["tool_calls"])
            lines.append(f"助手调用工具：{calls}")
        elif row["role"] == "tool":
            lines.append(f"工具结果（{row.get('tool')}）：{row['content']}")
        elif row["role"] == "ai":
            lines.append(f"助手：{row['content']}")
        elif row["role"] == "human":
            lines.append(f"用户：{row['content']}")
    return "\n".join(lines)


def parse_judgement(text: str) -> dict[str, Any]:
    """Pull the JSON object out of the judge's reply and check its shape. Raises ValueError if it is unusable."""
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match is None:
        raise ValueError(f"judge reply has no JSON: {text[:200]}")
    data = json.loads(match.group(0))
    score = data.get("score")
    if isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 5:
        raise ValueError(f"score must be an integer from 1 to 5, got {score!r}")
    reason = data.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("reason must be a non-empty string")
    return {"score": score, "reason": reason.strip()}


def judge_trace(trace: dict[str, Any], llm: Any, attempts: int = 3, pause_seconds: float = 20) -> dict[str, Any]:
    """Score one trace. Retries after a failed call or an unusable reply, pausing between tries for the free-tier rate limit."""
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            reply = llm.invoke([SystemMessage(JUDGE_PROMPT), HumanMessage(content=render_trace(trace))])
            return parse_judgement(to_text(reply.content))
        except Exception as exc:  # the judge can fail on quota, network or a malformed reply
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(pause_seconds)
    raise RuntimeError(f"trace {trace.get('id')} could not be judged: {last_error}") from last_error


def main() -> None:
    from cli import build_llm  # imported here so the judge module itself needs no API key

    traces = load_traces()
    if not traces:
        sys.exit(f"no traces in {TRACE_DIR}; run `python -m eval.record` first")
    llm = build_llm()
    results = []
    for trace in traces:
        judgement = judge_trace(trace, llm)
        results.append({"id": trace["id"], "question": trace["question"], **judgement})
        print(f"{trace['id']:<24} {judgement['score']}  {judgement['reason']}")
    average = sum(r["score"] for r in results) / len(results)
    RESULTS_PATH.write_text(json.dumps({"average": average, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{len(results)} traces, average {average:.2f}. Saved to {RESULTS_PATH}")


if __name__ == "__main__":
    main()
