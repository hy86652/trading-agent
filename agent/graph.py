"""Graph structure for Tasks 1–7.

START -> reasoner (or summarize first if the history is long)
reasoner -> pre_hook -> tool_exec -> post_hook -> reasoner (read-only tools)
pre_hook -> human_approval -> tool_exec -> post_hook -> reasoner (approved order)
human_approval -> reasoner (rejected order)
A failed pre-hook check also returns to the reasoner, and END is reached after
three consecutive failures. The checkpointer is required because the graph
pauses at human_approval until a person answers, and it keeps history across questions.

Tools come from an MCP server, discovered at startup. Every tool result passes
post_hook (summary, log, truncation) before the reasoner sees it.
"""
from pathlib import Path

from langgraph.graph import END, START, StateGraph

from agent.hooks import MAX_CONSECUTIVE_ERRORS, make_post_hook, make_pre_hook
from agent.mcp_client import MCPToolClient
from agent.nodes import MAX_MESSAGES, allowed_tools, human_approval, make_reasoner, make_summarizer, make_tool_exec
from agent.state import AgentState

TOOL_LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "logs" / "tool_calls.jsonl"


def route_to_reasoner(state: AgentState) -> str:
    """Compress the history first when it is too long; otherwise go straight to the reasoner."""
    return "summarize" if len(state["messages"]) > MAX_MESSAGES else "reasoner"


def route_after_reasoner(state: AgentState) -> str:
    """Send requested tool calls to the pre-hook, otherwise finish with the model's text answer."""
    last = state["messages"][-1]
    return "pre_hook" if getattr(last, "tool_calls", None) else END


def route_after_pre_hook(state: AgentState) -> str:
    """Stop after too many failures, return failures to the reasoner, pause for orders, run other tools."""
    if state["error_count"] >= MAX_CONSECUTIVE_ERRORS:
        return END
    if state["error_count"] > 0:
        return "reasoner"
    if state["pending_order"]:
        return "human_approval"
    return "tool_exec"


def route_after_approval(state: AgentState) -> str:
    """Execute the order only if it was approved; a rejection goes back to the reasoner, via the length check."""
    return "tool_exec" if state["approval"] == "approve" else route_to_reasoner(state)


def build_graph(llm, mcp: MCPToolClient, checkpointer=None, log_path: Path = TOOL_LOG_PATH):
    """Compile the agent graph around the chat model, the MCP tool server and a checkpointer."""
    tools = allowed_tools(mcp.langchain_tools())
    builder = StateGraph(AgentState)
    builder.add_node("reasoner", make_reasoner(llm, tools))
    builder.add_node("summarize", make_summarizer(llm))
    builder.add_node("pre_hook", make_pre_hook(mcp))
    builder.add_node("human_approval", human_approval)
    builder.add_node("tool_exec", make_tool_exec(tools, mcp.call))
    builder.add_node("post_hook", make_post_hook(log_path))
    builder.add_conditional_edges(START, route_to_reasoner, ["summarize", "reasoner"])
    builder.add_edge("summarize", "reasoner")
    builder.add_conditional_edges("reasoner", route_after_reasoner, ["pre_hook", END])
    builder.add_conditional_edges("pre_hook", route_after_pre_hook, ["reasoner", "human_approval", "tool_exec", END])
    builder.add_conditional_edges("human_approval", route_after_approval, ["tool_exec", "summarize", "reasoner"])
    builder.add_edge("tool_exec", "post_hook")
    builder.add_conditional_edges("post_hook", route_to_reasoner, ["summarize", "reasoner"])
    return builder.compile(checkpointer=checkpointer)
