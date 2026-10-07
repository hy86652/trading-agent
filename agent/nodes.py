"""Graph nodes: the reasoner decides, the pre-hook checks, a human approves, the executor acts (Task 2–6).

The reasoner never runs a tool itself. It only emits tool-call requests or a
final answer. Orders pass the pre-hook (hooks.py) and a human approval before
the executor sends them to the MCP server.
"""
import json
from typing import Any, Callable

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.types import interrupt

from agent.state import AgentState

# The server offers four tools and the LLM may see all of them. place_order is only
# executed after the pre-hook passes and a human approves it (see graph.py).
ALLOWED_TOOL_NAMES = frozenset({"get_price", "get_indicators", "get_portfolio", "place_order"})

# Once the history is longer than MAX_MESSAGES, the oldest SUMMARIZE_COUNT messages are compressed (brief section 6).
MAX_MESSAGES = 10
SUMMARIZE_COUNT = 8

SUMMARY_PROMPT = """你是对话压缩助手。把下面的对话压缩成一段简短的中文摘要。
保留：用户提出的请求、已经做出的决定（包括订单是否批准或拒绝）、重要结论。
只写对话里出现过的事实，不要补充对话之外的价格、指标或持仓数值。"""

SYSTEM_PROMPT = """你是模拟交易助手。全程只使用模拟数据与模拟账户。

规则：
1. 所有价格、指标、持仓数值必须来自工具返回，不得自行估算或编造。
2. 需要数据时调用工具获取；不要凭记忆回答行情或账户问题。
3. 如果工具返回 status 为 400 或含 error，根据错误信息修正参数后重试；无法修正时如实告知用户。
4. 买入或卖出必须调用 place_order，且每次只调用一个 place_order。订单需要用户批准，不要声称已经成交，以工具返回为准。
5. 如果工具返回 status 为 rejected，说明用户拒绝了这笔订单。如实告知用户，不要重复提交同一订单。
6. 你只负责决定下一步做什么，不要假装自己执行了任何操作。"""


def allowed_tools(discovered: list[StructuredTool]) -> list[StructuredTool]:
    """Keep only the tools the LLM is allowed to see."""
    return [t for t in discovered if t.name in ALLOWED_TOOL_NAMES]


def to_text(content: str | list) -> str:
    """Gemini may return a list of content blocks instead of a plain string; keep only the text parts."""
    if isinstance(content, str):
        return content
    return "".join(block.get("text", "") for block in content if isinstance(block, dict))


def make_reasoner(llm: Any, tools: list[StructuredTool]) -> Callable[[AgentState], dict[str, list]]:
    """Return the reasoner node with the given tools bound to the LLM."""
    model = llm.bind_tools(tools)

    def reasoner(state: AgentState) -> dict[str, list]:
        # The system prompt and the summary are prepended per call, not stored in messages,
        # so they never accumulate in the history.
        system = SYSTEM_PROMPT
        if state.get("summary"):
            system += f"\n\n早前对话摘要（仅供参考，数值以工具返回为准）：\n{state['summary']}"
        response = model.invoke([SystemMessage(system)] + state["messages"])
        return {"messages": [response]}

    return reasoner


def make_summarizer(llm: Any) -> Callable[[AgentState], dict[str, Any]]:
    """Return the summarize node. It compresses the oldest messages with a separate LLM call (no tools bound)."""

    def summarize(state: AgentState) -> dict[str, Any]:
        messages = state["messages"]
        cut = SUMMARIZE_COUNT
        # Never keep a tool result whose tool call was compressed away; the model rejects orphaned results.
        while cut < len(messages) and isinstance(messages[cut], ToolMessage):
            cut += 1
        old = messages[:cut]
        transcript = "\n".join(_describe(m) for m in old)
        prompt = [
            SystemMessage(SUMMARY_PROMPT),
            HumanMessage(content=f"已有摘要：{state.get('summary') or '无'}\n\n需要压缩的对话：\n{transcript}"),
        ]
        summary = to_text(llm.invoke(prompt).content)
        return {"summary": summary, "messages": [RemoveMessage(id=m.id) for m in old]}

    return summarize


def _describe(message: Any) -> str:
    """One line per message for the summary prompt, including the tool calls the model made."""
    role = {"human": "用户", "ai": "助手", "tool": "工具结果"}.get(message.type, message.type)
    text = to_text(message.content)
    if isinstance(message, AIMessage) and message.tool_calls:
        text += f" [调用工具: {', '.join(c['name'] for c in message.tool_calls)}]"
    return f"{role}: {text}"


def human_approval(state: AgentState) -> dict[str, Any]:
    """Pause the graph until a person approves or rejects the pending order.

    Anything other than "approve" counts as a rejection, so an unclear answer never trades.
    On resume the node runs again from the top, so nothing before interrupt() may have side effects.
    """
    order = state["pending_order"]
    decision = interrupt({"order": order["args"], "amount": order["amount"]})
    if str(decision).strip().lower() == "approve":
        return {"approval": "approve", "pending_order": None}
    # Answer the pending tool call so the message history stays valid for the reasoner.
    rejected = ToolMessage(
        content=json.dumps(
            {"status": "rejected", "error": "the user rejected this order; do not resubmit it unless asked"},
            ensure_ascii=False,
        ),
        tool_call_id=order["call_id"],
        name="place_order",
    )
    return {"approval": "reject", "pending_order": None, "messages": [rejected]}


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
