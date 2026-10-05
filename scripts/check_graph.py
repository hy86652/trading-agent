"""Offline check of the Task 1–3 loop using a scripted LLM, so no API key is needed.

Run from the project root with: python -m scripts.check_graph
"""
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.graph import build_graph


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


def check_tool_round_trip() -> None:
    """A tool request is executed and its result is fed back before the final answer."""
    llm = ScriptedLLM([
        AIMessage(content="", tool_calls=[
            {"name": "get_indicators", "args": {"ticker": "AAPL", "indicators": ["MA5", "RSI14"]}, "id": "call_1"},
        ]),
        AIMessage(content="AAPL 的 MA5 与 RSI14 已获取。"),
    ])
    graph = build_graph(llm)
    assert llm.bound == {"get_price", "get_indicators", "get_portfolio"}, llm.bound
    assert "place_order" not in llm.bound

    state = graph.invoke(initial_state("分析一下 AAPL 的趋势"))
    msgs = state["messages"]
    assert len(msgs) == 4, [type(m).__name__ for m in msgs]
    assert isinstance(msgs[2], ToolMessage) and msgs[2].tool_call_id == "call_1"
    assert "MA5" in msgs[2].content and "RSI14" in msgs[2].content, msgs[2].content
    assert msgs[-1].content == "AAPL 的 MA5 与 RSI14 已获取。"


def check_unknown_tool_returns_error() -> None:
    """A tool the agent does not have yields an error result instead of crashing the graph."""
    llm = ScriptedLLM([
        AIMessage(content="", tool_calls=[
            {"name": "place_order", "args": {"ticker": "AAPL", "side": "buy", "quantity": 1}, "id": "call_2"},
        ]),
        AIMessage(content="当前无法下单。"),
    ])
    state = build_graph(llm).invoke(initial_state("买 1 股 AAPL"))
    tool_msg = state["messages"][2]
    assert "unknown tool 'place_order'" in tool_msg.content, tool_msg.content
    assert state["messages"][-1].content == "当前无法下单。"


def check_bad_arguments_return_error() -> None:
    """Wrong argument names produce a correctable error for the reasoner."""
    llm = ScriptedLLM([
        AIMessage(content="", tool_calls=[
            {"name": "get_price", "args": {"symbol": "AAPL"}, "id": "call_3"},
        ]),
        AIMessage(content="参数有误。"),
    ])
    state = build_graph(llm).invoke(initial_state("AAPL 价格"))
    assert "bad arguments for get_price" in state["messages"][2].content, state["messages"][2].content


def main() -> None:
    check_tool_round_trip()
    check_unknown_tool_returns_error()
    check_bad_arguments_return_error()
    print("graph checks passed")


if __name__ == "__main__":
    main()
