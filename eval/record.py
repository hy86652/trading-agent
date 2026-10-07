"""Record traces for the judge: run each case in eval/cases.json through the agent and save the conversation.

Each case gets a fresh thread. When the agent asks for approval, the case's "decision" answers it,
so cases default to "reject" and the simulated account is not changed.
Uses the real model, so it consumes the Gemini quota. Run from the project root with: python -m eval.record
"""
import json
import uuid
from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agent.graph import build_graph
from agent.mcp_client import MCPToolClient
from cli import build_llm, question_input
from eval.judge import TRACE_DIR, transcript_from_messages

CASES_PATH = Path(__file__).resolve().parent / "cases.json"


def record_case(graph, case: dict) -> dict:
    """Run one case to completion and return its trace."""
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    result = graph.invoke(question_input(case["question"], resuming=False), config)
    while "__interrupt__" in result:
        result = graph.invoke(Command(resume=case["decision"]), config)
    return {
        "id": case["id"],
        "question": case["question"],
        "decision": case["decision"],
        "transcript": transcript_from_messages(result["messages"]),
    }


def main() -> None:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    with MCPToolClient() as mcp:
        # In-memory checkpoints are enough here: each case finishes in one run.
        graph = build_graph(build_llm(), mcp, MemorySaver())
        for case in cases:
            trace = record_case(graph, case)
            (TRACE_DIR / f"{case['id']}.json").write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"recorded {case['id']} ({len(trace['transcript'])} messages)")


if __name__ == "__main__":
    main()
