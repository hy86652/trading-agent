"""Local terminal entry point. A session is one thread: history is kept across questions and survives restarts.

Start a new session with `python cli.py`. Resume one with `python cli.py --thread <id>`; the id is printed at start.
Orders pause for approval in the terminal.
"""
import argparse
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from langfuse import get_client
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from agent.graph import build_graph
from agent.mcp_client import MCPToolClient
from agent.nodes import to_text
from agent.observability import disable_tracing_without_keys

ROOT = Path(__file__).resolve().parent
# Gitignored (*.db). Conversation state is kept here, so a restarted session can resume.
CHECKPOINT_PATH = ROOT / "data" / "checkpoints.db"

# Provider chosen: Gemini. Changing the model means editing this one constant.
GEMINI_MODEL = "gemini-3.8-flash"  # gemini-2.5-flash returned 404 for new users; check the Gemini API model list if this 404s


def build_llm():
    """Create the chat model. langchain-google-genai reads GOOGLE_API_KEY from the environment."""
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(model=GEMINI_MODEL, temperature=0)


def question_input(question: str, resuming: bool) -> dict:
    """Input for one question. Per-question fields are reset; history and summary are kept when resuming."""
    state = {
        "messages": [HumanMessage(content=question)],
        "pending_order": None,
        "approval": None,
        "error_count": 0,
    }
    if not resuming:
        state["summary"] = ""
    return state


def ask_approval(payload: dict) -> str:
    """Show the pending order and wait for approve or reject. Only those two answers are accepted."""
    order = payload["order"]
    print(f"\n[需要审批] 模拟{'买入' if order['side'] == 'buy' else '卖出'} {order['ticker']} × {order['quantity']} 股，约 {payload['amount']}")
    while True:
        decision = input("输入 approve 批准 / reject 拒绝 > ").strip().lower()
        if decision in ("approve", "reject"):
            return decision


def main() -> None:
    parser = argparse.ArgumentParser(description="模拟交易助手")
    parser.add_argument("--thread", help="resume the session with this id")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    disable_tracing_without_keys()
    CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    thread_id = args.thread or str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    langfuse = get_client()
    # The MCP server runs as a subprocess for the whole session and is shut down on exit.
    with MCPToolClient() as mcp, closing(sqlite3.connect(CHECKPOINT_PATH, check_same_thread=False)) as conn:
        graph = build_graph(build_llm(), mcp, SqliteSaver(conn))
        resuming = bool(graph.get_state(config).values)
        print(f"模拟交易助手（会话 {thread_id}{'，已恢复' if resuming else '，新会话'}）。输入 quit 退出。")
        while True:
            question = input("> ").strip()
            if question.lower() in ("quit", "exit"):
                break
            if not question:
                continue
            try:
                # One Langfuse trace per question. Without keys this is a no-op.
                with langfuse.start_as_current_observation(
                    name="question", as_type="span", input=question, metadata={"thread_id": thread_id}
                ) as trace:
                    result = graph.invoke(question_input(question, resuming), config)
                    resuming = True
                    # The graph pauses at human_approval; resume it with the person's decision.
                    while "__interrupt__" in result:
                        decision = ask_approval(result["__interrupt__"][0].value)
                        result = graph.invoke(Command(resume=decision), config)
                    answer = to_text(result["messages"][-1].content)
                    trace.update(output=answer)
            except Exception as exc:
                # A failed model call (quota, network) should not end the session. The thread is checkpointed,
                # so the conversation can continue once the problem clears.
                print(f"\n[出错] 本次请求未完成：{type(exc).__name__}: {str(exc)[:200]}")
                print("会话仍然保留，可以稍后继续提问（配额错误通常需要等到次日重置）。")
                continue
            print(answer)
        langfuse.flush()


if __name__ == "__main__":
    main()
