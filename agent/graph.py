"""Graph structure for Tasks 1–6.

reasoner -> pre_hook -> tool_exec (read-only tools) -> reasoner
pre_hook -> human_approval -> tool_exec (approved order) -> reasoner
human_approval -> reasoner (rejected order)
A failed pre-hook check also returns to the reasoner, and END is reached after
three consecutive failures. The checkpointer is required because the graph
pauses at human_approval until a person answers.

Tools come from an MCP server, discovered at startup. Later tasks insert post_hook
and summarize between these nodes.
"""
from langgraph.graph import END, START, StateGraph

from agent.hooks import MAX_CONSECUTIVE_ERRORS, make_pre_hook
from agent.mcp_client import MCPToolClient
from agent.nodes import allowed_tools, human_approval, make_reasoner, make_tool_exec
from agent.state import AgentState


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
    """Execute the order only if it was approved."""
    return "tool_exec" if state["approval"] == "approve" else "reasoner"


def build_graph(llm, mcp: MCPToolClient, checkpointer=None):
    """Compile the agent graph around the chat model, the MCP tool server and a checkpointer."""
    tools = allowed_tools(mcp.langchain_tools())
    builder = StateGraph(AgentState)
    builder.add_node("reasoner", make_reasoner(llm, tools))
    builder.add_node("pre_hook", make_pre_hook(mcp))
    builder.add_node("human_approval", human_approval)
    builder.add_node("tool_exec", make_tool_exec(tools, mcp.call))
    builder.add_edge(START, "reasoner")
    builder.add_conditional_edges("reasoner", route_after_reasoner, ["pre_hook", END])
    builder.add_conditional_edges("pre_hook", route_after_pre_hook, ["reasoner", "human_approval", "tool_exec", END])
    builder.add_conditional_edges("human_approval", route_after_approval, ["tool_exec", "reasoner"])
    builder.add_edge("tool_exec", "reasoner")
    return builder.compile(checkpointer=checkpointer)
