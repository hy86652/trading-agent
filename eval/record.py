"""Record traces for the judge: run each case in eval/cases.json through the agent and save the conversation.

Each case gets a fresh thread. When the agent asks for approval, the case's "decision" answers it,
so cases default to "reject" and the simulated account is not changed.
A failed case (quota, network) is retried a few times with a pause, then skipped and reported.
Uses the real model, so it consumes the Gemini quota. Run from the project root with: python -m eval.record
"""
import json
import sys
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agent.graph import build_graph
from agent.mcp_client import MCPToolClient
from agent.observability import disable_tracing_without_keys
from agent.runtime import ROOT, build_llm, question_input
from eval.judge import TRACE_DIR, transcript_from_messages

CASES_PATH = Path(__file__).resolve().parent / "cases.json"
ATTEMPTS = 3
PAUSE_SECONDS = 20  # free-tier rate limits reset per minute or per day; a short pause helps with brief spikes


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


def record_with_retries(graph, case: dict, attempts: int = ATTEMPTS, pause_seconds: float = PAUSE_SECONDS) -> dict:
    """Retry a case when the model or a tool call fails. Raises the last error if every attempt fails."""
    for attempt in range(1, attempts + 1):
        try:
            return record_case(graph, case)
        except Exception as exc:
            print(f"  attempt {attempt} for {case['id']} failed: {type(exc).__name__}", file=sys.stderr)
            if attempt == attempts:
                raise
            time.sleep(pause_seconds)
    raise AssertionError("unreachable")


def main() -> None:
    load_dotenv(ROOT / ".env")
    disable_tracing_without_keys()
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    failed: list[str] = []
    with MCPToolClient() as mcp:
        # In-memory checkpoints are enough here: each case finishes in one run.
        graph = build_graph(build_llm(), mcp, MemorySaver())
        for case in cases:
            try:
                trace = record_with_retries(graph, case)
            except Exception:
                failed.append(case["id"])
                continue
            (TRACE_DIR / f"{case['id']}.json").write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"recorded {case['id']} ({len(trace['transcript'])} messages)")
    if failed:
        sys.exit(f"not recorded after {ATTEMPTS} attempts: {', '.join(failed)}")


if __name__ == "__main__":
    main()
