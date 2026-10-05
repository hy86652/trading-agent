"""Graph structure for Tasks 1–3: reasoner <-> tool_exec until the model gives a final answer.

Later tasks insert pre_hook, human_approval, post_hook and summarize between these nodes.
"""
from typing import Any

from langgraph.graph import END, START, StateGraph

from agent.nodes import make_reasoner, tool_exec
from agent.state import AgentState


def route_after_reasoner(state: AgentState) -> str:
    """Go to the executor if the model asked for tools, otherwise finish with its text answer."""
    last = state["messages"][-1]
    return "tool_exec" if getattr(last, "tool_calls", None) else END


def build_graph(llm: Any):
    """Compile the agent graph around the given chat model."""
    builder = StateGraph(AgentState)
    builder.add_node("reasoner", make_reasoner(llm))
    builder.add_node("tool_exec", tool_exec)
    builder.add_edge(START, "reasoner")
    builder.add_conditional_edges("reasoner", route_after_reasoner, ["tool_exec", END])
    builder.add_edge("tool_exec", "reasoner")
    return builder.compile()
