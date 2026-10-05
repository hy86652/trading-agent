"""Offline check of the Task 1–4 loop using a scripted LLM, so no API key is needed.

The MCP server is started for real (it only reads local CSVs), so the tool
discovery and tools/call path are exercised too.

Run from the project root with: python -m scripts.check_graph
"""
import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.graph import build_graph
from agent.mcp_client import MCPToolClient
from agent.nodes import allowed_tools


class ScriptedLLM:
    """Replays canned replies so the reasoner <-> tool_exec loop can be exercised offline."""

    def __init__(self, replies: list[AIMessage]) -> None:
        self._replies = list(replies)
        self.bound: set[str] = set()

    def bind_tools(self, tools):
        self.bound = {t.name for t in tools}
        return self

    def invoke(self, messages):
        return self._replies.pop(0)


def initial_state(question: str) -> dict:
    return {
        "messages": [HumanMessage(content=question)],
        "summary": "",
        "pending_order": None,
        "approval": None,
        "error_count": 0,
    }


def check_discovery(mcp: MCPToolClient) -> None:
    """The server advertises all four tools, but the agent binds only the read-only ones."""
    discovered = {t.name for t in mcp.langchain_tools()}
    assert discovered == {"get_price", "get_indicators", "get_portfolio", "place_order"}, discovered
    assert {t.name for t in allowed_tools(mcp.langchain_tools())} == {"get_price", "get_indicators", "get_portfolio"}


def check_tool_round_trip(mcp: MCPToolClient) -> None:
    """A tool request goes over MCP, and its result is fed back before the final answer."""
    llm = ScriptedLLM([
        AIMessage(content="", tool_calls=[
            {"name": "get_indicators", "args": {"ticker": "AAPL", "indicators": ["MA5", "RSI14"]}, "id": "call_1"},
        ]),
        AIMessage(content="AAPL 的 MA5 与 RSI14 已获取。"),
    ])
    graph = build_graph(llm, mcp)
    assert llm.bound == {"get_price", "get_indicators", "get_portfolio"}, llm.bound
    assert "place_order" not in llm.bound

    state = graph.invoke(initial_state("分析一下 AAPL 的趋势"))
    msgs = state["messages"]
    assert len(msgs) == 4, [type(m).__name__ for m in msgs]
    assert isinstance(msgs[2], ToolMessage) and msgs[2].tool_call_id == "call_1"
    values = json.loads(msgs[2].content)["values"]
    assert values["MA5"] == 184.68, values  # same value as the stage 1 check against the CSV
    assert msgs[-1].content == "AAPL 的 MA5 与 RSI14 已获取。"


def check_unknown_tool_returns_error(mcp: MCPToolClient) -> None:
    """A tool the agent does not bind yields an error result, even though the server offers it."""
    llm = ScriptedLLM([
        AIMessage(content="", tool_calls=[
            {"name": "place_order", "args": {"ticker": "AAPL", "side": "buy", "quantity": 1}, "id": "call_2"},
        ]),
        AIMessage(content="当前无法下单。"),
    ])
    state = build_graph(llm, mcp).invoke(initial_state("买 1 股 AAPL"))
    tool_msg = state["messages"][2]
    assert "unknown tool 'place_order'" in tool_msg.content, tool_msg.content
    assert state["messages"][-1].content == "当前无法下单。"


def check_bad_arguments_return_error(mcp: MCPToolClient) -> None:
    """Wrong argument names produce a correctable error for the reasoner, not a crash."""
    llm = ScriptedLLM([
        AIMessage(content="", tool_calls=[
            {"name": "get_price", "args": {"symbol": "AAPL"}, "id": "call_3"},
        ]),
        AIMessage(content="参数有误。"),
    ])
    state = build_graph(llm, mcp).invoke(initial_state("AAPL 价格"))
    assert "error" in json.loads(state["messages"][2].content), state["messages"][2].content


def main() -> None:
    with MCPToolClient() as mcp:
        check_discovery(mcp)
        check_tool_round_trip(mcp)
        check_unknown_tool_returns_error(mcp)
        check_bad_arguments_return_error(mcp)
    print("graph checks passed")


if __name__ == "__main__":
    main()
