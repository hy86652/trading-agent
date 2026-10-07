"""Offline check of the Task 1–6 loop using a scripted LLM, so no API key is needed.

The MCP server is started for real, so tool discovery and tools/call are exercised.
The approval check changes the simulated account, so portfolio.json is restored afterwards.

Run from the project root with: python -m scripts.check_graph
"""
import contextlib
import json
import sqlite3
import tempfile
import uuid
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from agent.graph import build_graph
from agent.hooks import MAX_CONSECUTIVE_ERRORS
from agent.mcp_client import MCPToolClient
from agent.nodes import allowed_tools, make_summarizer
from cli import question_input

PORTFOLIO_PATH = Path(__file__).resolve().parent.parent / "data" / "portfolio.json"


class ScriptedLLM:
    """Replays canned replies so the reasoner loop can be exercised offline. Records what it was sent."""

    def __init__(self, replies: list[AIMessage]) -> None:
        self._replies = list(replies)
        self.bound: set[str] = set()
        self.seen: list = []

    def bind_tools(self, tools):
        self.bound = {t.name for t in tools}
        return self

    def invoke(self, messages):
        self.seen = list(messages)
        return self._replies.pop(0)


def initial_state(question: str) -> dict:
    return {
        "messages": [HumanMessage(content=question)],
        "summary": "",
        "pending_order": None,
        "approval": None,
        "error_count": 0,
    }


def new_config() -> dict:
    return {"configurable": {"thread_id": str(uuid.uuid4())}}


def order_call(call_id: str, quantity: int, side: str = "buy", ticker: str = "AAPL") -> AIMessage:
    return AIMessage(content="", tool_calls=[
        {"name": "place_order", "args": {"ticker": ticker, "side": side, "quantity": quantity}, "id": call_id},
    ])


def tool_messages(state: dict) -> list[ToolMessage]:
    return [m for m in state["messages"] if isinstance(m, ToolMessage)]


def check_discovery(mcp: MCPToolClient) -> None:
    """The server offers four tools and all four are bound to the LLM."""
    discovered = {t.name for t in mcp.langchain_tools()}
    assert discovered == {"get_price", "get_indicators", "get_portfolio", "place_order"}, discovered
    assert {t.name for t in allowed_tools(mcp.langchain_tools())} == discovered


def check_tool_round_trip(mcp: MCPToolClient) -> None:
    """A read-only tool request goes over MCP and its result is fed back before the final answer."""
    llm = ScriptedLLM([
        AIMessage(content="", tool_calls=[
            {"name": "get_indicators", "args": {"ticker": "AAPL", "indicators": ["MA5", "RSI14"]}, "id": "call_1"},
        ]),
        AIMessage(content="AAPL 的 MA5 与 RSI14 已获取。"),
    ])
    graph = build_graph(llm, mcp, MemorySaver())
    assert llm.bound == {"get_price", "get_indicators", "get_portfolio", "place_order"}, llm.bound

    state = graph.invoke(initial_state("分析一下 AAPL 的趋势"), new_config())
    msgs = state["messages"]
    assert len(msgs) == 4, [type(m).__name__ for m in msgs]
    assert isinstance(msgs[2], ToolMessage) and msgs[2].tool_call_id == "call_1"
    assert json.loads(msgs[2].content)["values"]["MA5"] == 184.68  # same value checked against the CSV
    assert msgs[-1].content == "AAPL 的 MA5 与 RSI14 已获取。"


def check_unknown_tool_returns_error(mcp: MCPToolClient) -> None:
    """A tool the agent does not offer yields an error result instead of crashing the graph."""
    llm = ScriptedLLM([
        AIMessage(content="", tool_calls=[{"name": "sell_everything", "args": {}, "id": "call_2"}]),
        AIMessage(content="没有这个工具。"),
    ])
    state = build_graph(llm, mcp, MemorySaver()).invoke(initial_state("全部卖出"), new_config())
    assert "unknown tool 'sell_everything'" in tool_messages(state)[0].content, tool_messages(state)[0].content


def check_bad_arguments_return_error(mcp: MCPToolClient) -> None:
    """Wrong argument names produce a correctable error for the reasoner, not a crash."""
    llm = ScriptedLLM([
        AIMessage(content="", tool_calls=[{"name": "get_price", "args": {"symbol": "AAPL"}, "id": "call_3"}]),
        AIMessage(content="参数有误。"),
    ])
    state = build_graph(llm, mcp, MemorySaver()).invoke(initial_state("AAPL 价格"), new_config())
    assert "error" in json.loads(tool_messages(state)[0].content), tool_messages(state)[0].content


def check_bad_order_is_corrected_then_rejected(mcp: MCPToolClient) -> None:
    """Pre-hook returns 400 for bad orders, the reasoner retries, and a rejected order is reported back."""
    llm = ScriptedLLM([
        order_call("o1", quantity=0),  # 400: not a positive integer
        order_call("o2", quantity=111),  # 400: over 20% of total assets
        order_call("o3", quantity=1),  # passes; pauses for approval
        AIMessage(content="好的，订单已取消。"),
    ])
    graph = build_graph(llm, mcp, MemorySaver())
    config = new_config()
    result = graph.invoke(initial_state("买 1 股 AAPL"), config)
    assert "__interrupt__" in result, "a valid order must pause for approval"
    assert result["__interrupt__"][0].value["order"] == {"ticker": "AAPL", "side": "buy", "quantity": 1}

    result = graph.invoke(Command(resume="reject"), config)
    tools_seen = tool_messages(result)
    assert json.loads(tools_seen[0].content)["status"] == 400
    assert "positive integer" in tools_seen[0].content, tools_seen[0].content
    assert "20%" in tools_seen[1].content, tools_seen[1].content
    assert json.loads(tools_seen[2].content)["status"] == "rejected", tools_seen[2].content
    assert result["messages"][-1].content == "好的，订单已取消。"


def check_three_failures_stop_the_turn(mcp: MCPToolClient) -> None:
    """After three consecutive failed checks the graph ends with an explanation instead of looping."""
    llm = ScriptedLLM([order_call(f"x{i}", quantity=0) for i in range(MAX_CONSECUTIVE_ERRORS)])
    state = build_graph(llm, mcp, MemorySaver()).invoke(initial_state("买入"), new_config())
    assert state["error_count"] == MAX_CONSECUTIVE_ERRORS
    assert "连续 3 次" in state["messages"][-1].content, state["messages"][-1].content


def check_approved_order_is_executed(mcp: MCPToolClient) -> None:
    """Approving resumes the graph, the order goes to the MCP server, and the account changes."""
    original = PORTFOLIO_PATH.read_text(encoding="utf-8")
    try:
        # Start from an empty account so the expected position is known, whatever the real account holds.
        PORTFOLIO_PATH.write_text(json.dumps({"cash": 100000.0, "positions": {}}), encoding="utf-8")
        llm = ScriptedLLM([order_call("o4", quantity=1), AIMessage(content="已买入 1 股 AAPL。")])
        graph = build_graph(llm, mcp, MemorySaver())
        config = new_config()
        result = graph.invoke(initial_state("买 1 股 AAPL"), config)
        assert "__interrupt__" in result
        result = graph.invoke(Command(resume="approve"), config)

        filled = json.loads(tool_messages(result)[0].content)
        assert filled.get("status") == "filled", filled
        assert json.loads(PORTFOLIO_PATH.read_text(encoding="utf-8"))["positions"] == {"AAPL": 1}
        assert result["messages"][-1].content == "已买入 1 股 AAPL。"
    finally:
        PORTFOLIO_PATH.write_text(original, encoding="utf-8")


def _with_id(message):
    """Summarize removes messages by id; the graph assigns ids itself, so the offline test has to as well."""
    message.id = str(uuid.uuid4())
    return message


def check_summary_cut_keeps_tool_pairs() -> None:
    """The oldest 8 messages are compressed, but never a tool result away from its tool call."""
    llm = ScriptedLLM([AIMessage(content="摘要"), AIMessage(content="摘要")])  # one reply per summarize call below
    summarize = make_summarizer(llm)

    plain = [_with_id(HumanMessage(content=f"问题{i}")) for i in range(11)]
    removed = summarize({"messages": plain, "summary": ""})["messages"]
    assert len(removed) == 8, len(removed)

    # Index 7 calls a tool and index 8 is its result, so the cut must move past the result.
    paired = [
        _with_id(HumanMessage(content="a")),
        _with_id(AIMessage(content="b")),
        _with_id(HumanMessage(content="c")),
        _with_id(AIMessage(content="d")),
        _with_id(HumanMessage(content="e")),
        _with_id(AIMessage(content="f")),
        _with_id(HumanMessage(content="g")),
        _with_id(AIMessage(content="", tool_calls=[{"name": "get_price", "args": {"ticker": "AAPL"}, "id": "t"}])),
        _with_id(ToolMessage(content="{}", tool_call_id="t", name="get_price")),
        _with_id(AIMessage(content="h")),
        _with_id(HumanMessage(content="i")),
    ]
    removed = summarize({"messages": paired, "summary": ""})["messages"]
    assert len(removed) == 9, len(removed)


def check_long_history_is_summarized(mcp: MCPToolClient) -> None:
    """After 10 messages, the next question first compresses the history; the summary is kept in state."""
    replies = [AIMessage(content=f"回答{i}") for i in range(1, 6)]
    replies += [AIMessage(content="摘要：用户问过五个问题"), AIMessage(content="第六个回答")]
    llm = ScriptedLLM(replies)
    graph = build_graph(llm, mcp, MemorySaver())
    config = new_config()
    for i in range(1, 7):
        result = graph.invoke(question_input(f"问题{i}", resuming=i > 1), config)
    assert result["summary"] == "摘要：用户问过五个问题", result["summary"]
    assert len(result["messages"]) == 4, [type(m).__name__ for m in result["messages"]]
    assert result["messages"][-1].content == "第六个回答"


def check_history_survives_restart(mcp: MCPToolClient) -> None:
    """A new process (new SqliteSaver connection) resumes the same thread and still sees the earlier question."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "checkpoints.db"
        config = new_config()

        with contextlib.closing(sqlite3.connect(db, check_same_thread=False)) as conn:
            first = ScriptedLLM([AIMessage(content="我记下了：AAPL")])
            build_graph(first, mcp, SqliteSaver(conn)).invoke(question_input("关注 AAPL", resuming=False), config)

        with contextlib.closing(sqlite3.connect(db, check_same_thread=False)) as conn:
            second = ScriptedLLM([AIMessage(content="你刚才说关注 AAPL。")])
            graph = build_graph(second, mcp, SqliteSaver(conn))
            assert graph.get_state(config).values, "the thread must exist after a restart"
            result = graph.invoke(question_input("我刚才说关注哪只股票？", resuming=True), config)

        texts = [m.content for m in second.seen]
        assert "关注 AAPL" in texts, texts
        assert result["messages"][-1].content == "你刚才说关注 AAPL。"


def main() -> None:
    check_summary_cut_keeps_tool_pairs()
    with MCPToolClient() as mcp:
        check_discovery(mcp)
        check_tool_round_trip(mcp)
        check_unknown_tool_returns_error(mcp)
        check_bad_arguments_return_error(mcp)
        check_bad_order_is_corrected_then_rejected(mcp)
        check_three_failures_stop_the_turn(mcp)
        check_approved_order_is_executed(mcp)
        check_long_history_is_summarized(mcp)
        check_history_survives_restart(mcp)
    print("graph checks passed")


if __name__ == "__main__":
    main()
