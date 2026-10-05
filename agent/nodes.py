"""Graph nodes: the reasoner decides what to do, the tool executor does it (Task 2–3).

The reasoner never runs a tool itself. It only emits tool-call requests or a
final answer, and the executor carries out the requests.
"""
import json
from typing import Any, Callable

from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool

from agent.state import AgentState
from tools import get_indicators, get_portfolio, get_price

# place_order is deliberately absent. It needs the pre-hook (Task 5) and human
# approval (Task 6) before the agent is allowed to trade.
READ_ONLY_TOOLS: dict[str, Callable[..., dict[str, Any]]] = {
    "get_price": get_price,
    "get_indicators": get_indicators,
    "get_portfolio": get_portfolio,
}

SYSTEM_PROMPT = """你是模拟交易助手。全程只使用模拟数据与模拟账户。

规则：
1. 所有价格、指标、持仓数值必须来自工具返回，不得自行估算或编造。
2. 需要数据时调用工具获取；不要凭记忆回答行情或账户问题。
3. 如果工具返回 error，根据错误信息修正参数后重试；无法修正时如实告知用户。
4. 目前你还不能下单。如果用户要求买卖，请如实说明，不要声称已下单。
5. 你只负责决定下一步做什么，不要假装自己执行了任何操作。"""


def bound_tools() -> list[StructuredTool]:
    """Wrap the read-only tools as LangChain tools; the docstring becomes the description the LLM sees."""
    return [StructuredTool.from_function(fn, name=name) for name, fn in READ_ONLY_TOOLS.items()]


def make_reasoner(llm: Any) -> Callable[[AgentState], dict[str, list]]:
    """Return the reasoner node with the tools bound to the given LLM."""
    model = llm.bind_tools(bound_tools())

    def reasoner(state: AgentState) -> dict[str, list]:
        # The system prompt is prepended per call, not stored in messages,
        # so it never accumulates in the history.
        response = model.invoke([SystemMessage(SYSTEM_PROMPT)] + state["messages"])
        return {"messages": [response]}

    return reasoner


def _run_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Route one tool-call request to its Python function."""
    fn = READ_ONLY_TOOLS.get(name)
    if fn is None:
        return {"error": f"unknown tool '{name}', available: {sorted(READ_ONLY_TOOLS)}"}
    try:
        return fn(**args)
    except TypeError as exc:
        # The LLM supplied arguments the function does not accept. Returning this as data
        # lets the reasoner correct itself instead of crashing the graph.
        return {"error": f"bad arguments for {name}: {exc}"}


def tool_exec(state: AgentState) -> dict[str, list]:
    """Execute every tool call in the last AI message and append the results."""
    last = state["messages"][-1]
    results = []
    for call in last.tool_calls:
        output = _run_tool(call["name"], call["args"])
        results.append(
            ToolMessage(
                content=json.dumps(output, ensure_ascii=False),
                tool_call_id=call["id"],
                name=call["name"],
            )
        )
    return {"messages": results}
