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

    # The tool gives an ISO date; the model reformats it in prose. The day/month/year must still be grounded.
    reformatted = check({
        "id": "f2",
        "question": "What is the AAPL price?",
        "transcript": [
            {"role": "human", "content": "What is the AAPL price?"},
            {"role": "tool", "tool": "get_price", "content": '{"bars": [{"date": "2026-05-21", "close": 180.64}]}'},
            {"role": "ai", "content": "As of May 21, 2026, AAPL closed at $180.64."},
        ],
    })
    assert reformatted == {"checked": 3, "ungrounded": [], "passed": True}, reformatted

    # The tool gives a plain number; the model adds thousands separators. "8,807,415" must not read as
    # three small numbers ("8", "807", "415").
    comma_formatted = check({
        "id": "f3",
        "question": "What is the AAPL volume?",
        "transcript": [
            {"role": "human", "content": "What is the AAPL volume?"},
            {"role": "tool", "tool": "get_price", "content": '{"bars": [{"volume": 8807415, "close": 99819.36}]}'},
            {"role": "ai", "content": "Volume was 8,807,415 shares; cash on hand is $99,819.36."},
        ],
    })
    assert comma_formatted == {"checked": 2, "ungrounded": [], "passed": True}, comma_formatted


def check_langfuse_source() -> None:
    """A Langfuse trace is rebuilt into the same transcript shape the local recorder produces."""
    from types import SimpleNamespace

    from eval.faithfulness import check_faithfulness
    from eval.langfuse_source import (
        PRE_HOOK_SPAN, QUESTION_SPAN, REASONER_SPAN, TOOL_PREFIX, example_detail, fetch_question_traces,
        trace_from_langfuse,
    )

    # Rejected order: the human decision becomes a rejected tool result, and the final answer is not duplicated.
    trace = trace_from_langfuse(example_detail())
    assert [row["role"] for row in trace["transcript"]] == ["human", "ai", "tool", "ai"], trace["transcript"]
    assert trace["transcript"][1]["tool_calls"][0]["name"] == "place_order"
    assert trace["transcript"][2]["tool"] == "place_order" and "rejected" in trace["transcript"][2]["content"]
    assert trace["transcript"][-1]["content"] == "Order cancelled."
    assert check_faithfulness(trace)["passed"] is True

    # Pre-hook rejection and a read-only tool call, in order.
    detail = SimpleNamespace(id="x", input=None, output=None, observations=[
        SimpleNamespace(name=QUESTION_SPAN, start_time=0, input="What is AAPL?", output="It is 180.64."),
        SimpleNamespace(name=REASONER_SPAN, start_time=1, input=None, output={"content": "", "tool_calls": [
            {"name": "place_order", "args": {"ticker": "AAPL", "side": "buy", "quantity": 0}}]}),
        SimpleNamespace(name=PRE_HOOK_SPAN, start_time=2, input={"calls": ["place_order"]},
                        output={"status": 400, "error": "quantity must be a positive integer"}),
        SimpleNamespace(name=f"{TOOL_PREFIX}get_price", start_time=3,
                        input={"method": "tools/call", "params": {"name": "get_price", "arguments": {}}},
                        output={"result": {"ticker": "AAPL"}, "isError": False}),
        SimpleNamespace(name=REASONER_SPAN, start_time=4, input=None, output={"content": "It is 180.64.", "tool_calls": []}),
    ])
    trace = trace_from_langfuse(detail)
    roles = [(row["role"], row.get("tool")) for row in trace["transcript"]]
    assert roles == [("human", None), ("ai", None), ("tool", "place_order"), ("tool", "get_price"), ("ai", None)], roles
    assert "positive integer" in trace["transcript"][2]["content"]

    # The v2 endpoint returns input and output as raw strings. JSON is parsed; a plain numeric answer is kept as text.
    raw = SimpleNamespace(id="raw", observations=[
        SimpleNamespace(name=QUESTION_SPAN, start_time=0, input="What is the MA5?", output="184.68"),
        SimpleNamespace(name=REASONER_SPAN, start_time=1, input=None,
                        output='{"content": "", "tool_calls": [{"name": "get_price", "args": {"ticker": "AAPL"}}]}'),
        SimpleNamespace(name=f"{TOOL_PREFIX}get_price", start_time=2,
                        input='{"method": "tools/call", "params": {"name": "get_price", "arguments": {"ticker": "AAPL"}}}',
                        output='{"result": {"ticker": "AAPL", "bars": []}, "isError": false}'),
    ])
    trace = trace_from_langfuse(raw)
    assert trace["transcript"][2] == {"role": "tool", "tool": "get_price", "content": '{"ticker": "AAPL", "bars": []}'}, trace["transcript"][2]
    assert trace["transcript"][-1] == {"role": "ai", "content": "184.68"}, trace["transcript"][-1]

    # Without keys the source refuses to run, rather than returning nothing.
    try:
        fetch_question_traces(1)
    except RuntimeError as exc:
        assert "not configured" in str(exc)
    else:
        raise AssertionError("fetching must fail without Langfuse keys")


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
    check_langfuse_source()
    with MCPToolClient() as mcp:
        check_record_case(mcp)
    print("eval checks passed")


if __name__ == "__main__":
    main()
