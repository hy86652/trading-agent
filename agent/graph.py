"""Graph structure for Tasks 1–4: reasoner <-> tool_exec until the model gives a final answer.

Tools come from an MCP server, discovered at startup. Later tasks insert pre_hook,
human_approval, post_hook and summarize between these nodes.
"""
from langgraph.graph import END, START, StateGraph

from agent.mcp_client import MCPToolClient
from agent.nodes import allowed_tools, make_reasoner, make_tool_exec
from agent.state import AgentState


def route_after_reasoner(state: AgentState) -> str:
    """Go to the executor if the model asked for tools, otherwise finish with its text answer."""
    last = state["messages"][-1]
    return "tool_exec" if getattr(last, "tool_calls", None) else END


def build_graph(llm, mcp: MCPToolClient):
    """Compile the agent graph around the chat model and the connected MCP tool server."""
    tools = allowed_tools(mcp.langchain_tools())
    builder = StateGraph(AgentState)
    builder.add_node("reasoner", make_reasoner(llm, tools))
    builder.add_node("tool_exec", make_tool_exec(tools, mcp.call))
    builder.add_edge(START, "reasoner")
    builder.add_conditional_edges("reasoner", route_after_reasoner, ["tool_exec", END])
    builder.add_edge("tool_exec", "reasoner")
    return builder.compile()
