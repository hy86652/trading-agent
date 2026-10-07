"""Offline check of the evaluation code: judge parsing and retries, and trace recording. No API key needed.

Run from the project root with: python -m scripts.check_eval
"""
import json
import os

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
    assert parse_judgement('Sure: {"score": 4, "reason": "good recovery"}') == {"score": 4, "reason": "good recovery"}
    for bad in ['{"score": 6, "reason": "x"}', '{"score": true, "reason": "x"}', '{"score": 3, "reason": ""}', "no json here"]:
        try:
            parse_judgement(bad)
        except ValueError:
            continue
        raise AssertionError(f"should have been rejected: {bad}")


def check_judge_retries_bad_replies() -> None:
    """A malformed reply is retried; the first usable reply wins."""
    trace = {"id": "t", "question": "q", "transcript": [{"role": "human", "content": "q"}]}
    llm = ScriptedLLM([AIMessage(content="not JSON"), AIMessage(content='{"score": 2, "reason": "did not fix arguments"}')])
    result = judge_trace(trace, llm, attempts=3, pause_seconds=0)
    assert result == {"score": 2, "reason": "did not fix arguments"}, result


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
        AIMessage(content="OK, cancelled."),
    ])
    graph = build_graph(llm, mcp, MemorySaver())
    trace = record_case(graph, {"id": "x", "question": "Buy 1 share of AAPL", "decision": "reject"})
    roles = [row["role"] for row in trace["transcript"]]
    assert roles == ["human", "ai", "tool", "ai"], roles
    assert trace["transcript"][2]["tool"] == "place_order"
    assert "rejected" in trace["transcript"][2]["content"]
    text = render_trace(trace)
    assert "Assistant calls tools: place_order" in text and "OK, cancelled." in text, text


def check_record_retries_failed_case() -> None:
    """A case that fails once (quota or network) is retried, and the second attempt is recorded."""
    from langchain_core.messages import HumanMessage

    from eval.record import record_with_retries

    class FlakyGraph:
        def __init__(self) -> None:
            self.calls = 0

        def invoke(self, state, config=None):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("429 quota")
            return {"messages": [HumanMessage(content="question"), AIMessage(content="answer")]}

    graph = FlakyGraph()
    trace = record_with_retries(graph, {"id": "r", "question": "question", "decision": "reject"}, attempts=2, pause_seconds=0)
    assert graph.calls == 2, graph.calls
    assert trace["transcript"][-1]["content"] == "answer"


def _trace_with_answer(answer: str) -> dict:
    return {
        "id": "f",
        "question": "What is the MA5 of AAPL?",
        "transcript": [
            {"role": "human", "content": "What is the MA5 of AAPL?"},
            {"role": "ai", "content": "", "tool_calls": [{"name": "get_indicators", "args": {}}]},
            {"role": "tool", "tool": "get_indicators", "content": '{"ticker": "AAPL", "date": "2026-01-02", "values": {"MA5": 184.68}}'},
            {"role": "ai", "content": answer},
        ],
    }


def check_faithfulness() -> None:
    """Numbers in the answer must appear in the question or tool results; labels such as MA5 are ignored."""
    from eval.faithfulness import check_faithfulness as check, numbers_in

    grounded = check(_trace_with_answer("The MA5 of AAPL is 184.68 as of 2026-01-02."))
    assert grounded == {"checked": 2, "ungrounded": [], "passed": True}, grounded  # 184.68 and the year 2026

    invented = check(_trace_with_answer("The MA5 of AAPL is 190.00."))
    assert invented["passed"] is False and invented["ungrounded"] == [190.0], invented

    assert numbers_in("MA5 and RSI14 over 20 days") == [20.0], numbers_in("MA5 and RSI14 over 20 days")


def check_transcript_is_json_serialisable() -> None:
    from langchain_core.messages import HumanMessage

    rows = transcript_from_messages([HumanMessage(content="question"), AIMessage(content="answer")])
    assert json.loads(json.dumps(rows, ensure_ascii=False)) == rows


def main() -> None:
    os.environ["LANGFUSE_TRACING_ENABLED"] = "false"  # offline checks never send traces
    check_parse_judgement()
    check_judge_retries_bad_replies()
    check_judge_gives_up_after_attempts()
    check_transcript_is_json_serialisable()
    check_record_retries_failed_case()
    check_faithfulness()
    with MCPToolClient() as mcp:
        check_record_case(mcp)
    print("eval checks passed")


if __name__ == "__main__":
    main()
