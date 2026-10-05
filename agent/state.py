"""Shared memory object passed between graph nodes (Task 1).

Every field is defined now so later tasks add behaviour without changing the
shape of the state. Nodes return partial updates; LangGraph merges them.
"""
from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]  # conversation and tool results
    summary: str  # compressed history, used from Task 7
    pending_order: dict | None  # order waiting for approval, used from Task 6
    approval: str | None  # "approve" / "reject" / None
    error_count: int  # consecutive errors, guards against retry loops (Task 5)
