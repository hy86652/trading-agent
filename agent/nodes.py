"""Graph nodes: the reasoner decides what to do, the tool executor does it (Task 2–4).

The reasoner never runs a tool itself. It only emits tool-call requests or a
final answer, and the executor carries out the requests through the MCP client.
"""
import json
from typing import Any, Callable

from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool

from agent.state import AgentState

# Which discovered tools the LLM may call. The server offers all four; the agent
# decides. place_order stays out until the pre-hook (Task 5) and human approval (Task 6) exist.
READ_ONLY_TOOL_NAMES = frozenset({"get_price", "get_indicators", "get_portfolio"})

SYSTEM_PROMPT = """你是模拟交易助手。全程只使用模拟数据与模拟账户。

规则：
1. 所有价格、指标、持仓数值必须来自工具返回，不得自行估算或编造。
2. 需要数据时调用工具获取；不要凭记忆回答行情或账户问题。
3. 如果工具返回 error，根据错误信息修正参数后重试；无法修正时如实告知用户。
4. 目前你还不能下单。如果用户要求买卖，请如实说明，不要声称已下单。
5. 你只负责决定下一步做什么，不要假装自己执行了任何操作。"""


def allowed_tools(discovered: list[StructuredTool]) -> list[StructuredTool]:
    """Keep only the tools the LLM is allowed to see."""
    return [t for t in discovered if t.name in READ_ONLY_TOOL_NAMES]


def make_reasoner(llm: Any, tools: list[StructuredTool]) -> Callable[[AgentState], dict[str, list]]:
    """Return the reasoner node with the given tools bound to the LLM."""
    model = llm.bind_tools(tools)

    def reasoner(state: AgentState) -> dict[str, list]:
        # The system prompt is prepended per call, not stored in messages,
        # so it never accumulates in the history.
        response = model.invoke([SystemMessage(SYSTEM_PROMPT)] + state["messages"])
        return {"messages": [response]}

    return reasoner


def make_tool_exec(tools: list[StructuredTool], call_tool: Callable[[str, dict[str, Any]], dict[str, Any]]):
    """Return the executor node. It only runs tools in `tools`, so the allow-list holds even if the LLM asks for others."""
    names = sorted(t.name for t in tools)

    def tool_exec(state: AgentState) -> dict[str, list]:
        """Execute every tool call in the last AI message and append the results."""
        last = state["messages"][-1]
        results = []
        for call in last.tool_calls:
            if call["name"] not in names:
                # Returned as data so the reasoner can correct itself instead of crashing the graph.
                output = {"error": f"unknown tool '{call['name']}', available: {names}"}
            else:
                output = call_tool(call["name"], call["args"])
            results.append(
                ToolMessage(
                    content=json.dumps(output, ensure_ascii=False),
                    tool_call_id=call["id"],
                    name=call["name"],
                )
            )
        return {"messages": results}

    return tool_exec
