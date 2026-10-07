"""Graph nodes: the reasoner decides, the pre-hook checks, a human approves, the executor acts (Tasks 2–6).

The reasoner never runs a tool itself. It only emits tool-call requests or a
final answer. Orders pass the pre-hook (hooks.py) and a human approval before
the executor sends them to the MCP server.
"""
import json
from typing import Any, Callable

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langfuse import get_client
from langgraph.types import interrupt

from agent.state import AgentState

# The server offers four tools and the LLM may see all of them. place_order is only
# executed after the pre-hook passes and a human approves it (see graph.py).
ALLOWED_TOOL_NAMES = frozenset({"get_price", "get_indicators", "get_portfolio", "place_order"})

# Once the history is longer than MAX_MESSAGES, the oldest SUMMARIZE_COUNT messages are compressed (brief section 6).
MAX_MESSAGES = 10
SUMMARIZE_COUNT = 8

SUMMARY_PROMPT = """You are a conversation compression assistant. Compress the conversation below into a short summary.
Keep: the user's requests, the decisions made (including whether an order was approved or rejected), and key conclusions.
Only record facts that appear in the conversation. Do not add any price, indicator or position values that are not in it.
Write the summary in the language the conversation is mostly written in."""

SYSTEM_PROMPT = """You are a simulated trading assistant. Use only simulated data and the simulated account at all times.

Rules:
1. Every price, indicator and position value must come from a tool result. Never estimate or make up values.
2. Call a tool to get data. Do not answer market or account questions from memory.
3. If a tool result has status 400 or contains an error, correct the arguments based on the error and retry. If you cannot correct it, tell the user honestly.
4. To buy or sell, call place_order, and call it only once per turn. The order needs the user's approval. Do not say the order was filled; the tool result is the only source of truth.
5. If a tool result has status "rejected", the user rejected that order. Tell the user, and do not resubmit the same order.
6. You only decide the next step. Do not pretend that you performed any action.
7. Reply in the same language as the user's latest question."""


def allowed_tools(discovered: list[StructuredTool]) -> list[StructuredTool]:
    """Keep only the tools the LLM is allowed to see."""
    return [t for t in discovered if t.name in ALLOWED_TOOL_NAMES]


def to_text(content: str | list) -> str:
    """Gemini may return a list of content blocks instead of a plain string; keep only the text parts."""
    if isinstance(content, str):
        return content
    return "".join(block.get("text", "") for block in content if isinstance(block, dict))


def record_llm_call(generation: Any, response: Any, model_name: str | None) -> None:
    """Attach the model name, output and token counts to a Langfuse generation span (a no-op without keys)."""
    usage = getattr(response, "usage_metadata", None) or {}
    generation.update(
        model=model_name,
        output=to_text(response.content) or str(getattr(response, "tool_calls", "")),
        usage_details={"input": usage.get("input_tokens", 0), "output": usage.get("output_tokens", 0)} if usage else None,
    )


def make_reasoner(llm: Any, tools: list[StructuredTool]) -> Callable[[AgentState], dict[str, list]]:
    """Return the reasoner node with the given tools bound to the LLM."""
    model = llm.bind_tools(tools)
    model_name = getattr(llm, "model", None)

    def reasoner(state: AgentState) -> dict[str, list]:
        # The system prompt and the summary are prepended per call, not stored in messages,
        # so they never accumulate in the history.
        system = SYSTEM_PROMPT
        if state.get("summary"):
            system += f"\n\nEarlier conversation summary (for reference only; values come from tools):\n{state['summary']}"
        with get_client().start_as_current_observation(
            name="llm_reasoner", as_type="generation", input=state["messages"][-1].content
        ) as generation:
            response = model.invoke([SystemMessage(system)] + state["messages"])
            record_llm_call(generation, response, model_name)
        return {"messages": [response]}

    return reasoner


def make_summarizer(llm: Any) -> Callable[[AgentState], dict[str, Any]]:
    """Return the summarize node. It compresses the oldest messages with a separate LLM call (no tools bound)."""
    model_name = getattr(llm, "model", None)

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
            HumanMessage(content=f"Existing summary: {state.get('summary') or 'none'}\n\nConversation to compress:\n{transcript}"),
        ]
        with get_client().start_as_current_observation(
            name="llm_summarize", as_type="generation", input=transcript
        ) as generation:
            response = llm.invoke(prompt)
            record_llm_call(generation, response, model_name)
        summary = to_text(response.content)
        return {"summary": summary, "messages": [RemoveMessage(id=m.id) for m in old]}

    return summarize


def _describe(message: Any) -> str:
    """One line per message for the summary prompt, including the tool calls the model made."""
    role = {"human": "User", "ai": "Assistant", "tool": "Tool result"}.get(message.type, message.type)
    text = to_text(message.content)
    if isinstance(message, AIMessage) and message.tool_calls:
        text += f" [called tools: {', '.join(c['name'] for c in message.tool_calls)}]"
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
