"""Offline check of the evaluation code: judge parsing and retries, and trace recording. No API key needed.

Run from the project root with: python -m scripts.check_eval
"""
import json

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from agent.graph import build_graph
from agent.mcp_client import MCPToolClient
from eval.judge import judge_trace, parse_judgement, render_trace, transcript_from_messages
from eval.record import record_case


class ScriptedLLM:
    """Replays canned replies. Used both as the agent model and as the judge in these checks."""

    def __init__(self, replies: list[AIMessage | Exception]) -> None:
        self._replies = list(replies)

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def check_parse_judgement() -> None:
    assert parse_judgement('好的：{"score": 4, "reason": "恢复良好"}') == {"score": 4, "reason": "恢复良好"}
    for bad in ['{"score": 6, "reason": "x"}', '{"score": true, "reason": "x"}', '{"score": 3, "reason": ""}', "no json here"]:
        try:
            parse_judgement(bad)
        except ValueError:
            continue
        raise AssertionError(f"should have been rejected: {bad}")


def check_judge_retries_bad_replies() -> None:
    """A malformed reply is retried; the first usable reply wins."""
    trace = {"id": "t", "question": "q", "transcript": [{"role": "human", "content": "q"}]}
    llm = ScriptedLLM([AIMessage(content="不是 JSON"), AIMessage(content='{"score": 2, "reason": "未修正参数"}')])
    result = judge_trace(trace, llm, attempts=3, pause_seconds=0)
    assert result == {"score": 2, "reason": "未修正参数"}, result


def check_judge_gives_up_after_attempts() -> None:
    trace = {"id": "t", "question": "q", "transcript": []}
    llm = ScriptedLLM([AIMessage(content="x"), AIMessage(content="y")])
    try:
        judge_trace(trace, llm, attempts=2, pause_seconds=0)
    except RuntimeError as exc:
        assert "could not be judged" in str(exc)
        return
    raise AssertionError("judge should give up after the configured attempts")


def check_record_case(mcp: MCPToolClient) -> None:
    """A recorded case captures the whole conversation, including the rejected order."""
    llm = ScriptedLLM([
        AIMessage(content="", tool_calls=[{"name": "place_order", "args": {"ticker": "AAPL", "side": "buy", "quantity": 1}, "id": "c1"}]),
        AIMessage(content="好的，已取消。"),
    ])
    graph = build_graph(llm, mcp, MemorySaver())
    trace = record_case(graph, {"id": "x", "question": "买 1 股 AAPL", "decision": "reject"})
    roles = [row["role"] for row in trace["transcript"]]
    assert roles == ["human", "ai", "tool", "ai"], roles
    assert trace["transcript"][2]["tool"] == "place_order"
    assert "rejected" in trace["transcript"][2]["content"]
    text = render_trace(trace)
    assert "助手调用工具：place_order" in text and "好的，已取消。" in text, text


def check_transcript_is_json_serialisable() -> None:
    from langchain_core.messages import HumanMessage

    rows = transcript_from_messages([HumanMessage(content="问"), AIMessage(content="答")])
    assert json.loads(json.dumps(rows, ensure_ascii=False)) == rows


def main() -> None:
    check_parse_judgement()
    check_judge_retries_bad_replies()
    check_judge_gives_up_after_attempts()
    check_transcript_is_json_serialisable()
    with MCPToolClient() as mcp:
        check_record_case(mcp)
    print("eval checks passed")


if __name__ == "__main__":
    main()
