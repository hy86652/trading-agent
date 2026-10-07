"""Local terminal entry point. Each question gets its own thread; orders pause for approval in the terminal."""
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from agent.graph import build_graph
from agent.mcp_client import MCPToolClient

ROOT = Path(__file__).resolve().parent
# Gitignored (*.db). Stage 4 relies on this file to resume after a restart.
CHECKPOINT_PATH = ROOT / "data" / "checkpoints.db"

# Provider chosen: Gemini. Changing the model means editing this one constant.
GEMINI_MODEL = "gemini-3.8-flash"  # gemini-2.5-flash returned 404 for new users; check the Gemini API model list if this 404s


def build_llm():
    """Create the chat model. langchain-google-genai reads GOOGLE_API_KEY from the environment."""
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(model=GEMINI_MODEL, temperature=0)


def to_text(content: str | list) -> str:
    """Gemini may return a list of content blocks instead of a plain string; keep only the text parts."""
    if isinstance(content, str):
        return content
    return "".join(block.get("text", "") for block in content if isinstance(block, dict))


def new_state(question: str) -> dict:
    """Initial state for one question; every field from state.py is present."""
    return {
        "messages": [HumanMessage(content=question)],
        "summary": "",
        "pending_order": None,
        "approval": None,
        "error_count": 0,
    }


def ask_approval(payload: dict) -> str:
    """Show the pending order and wait for approve or reject. Only those two answers are accepted."""
    order = payload["order"]
    print(f"\n[需要审批] 模拟{'买入' if order['side'] == 'buy' else '卖出'} {order['ticker']} × {order['quantity']} 股，约 {payload['amount']}")
    while True:
        decision = input("输入 approve 批准 / reject 拒绝 > ").strip().lower()
        if decision in ("approve", "reject"):
            return decision


def main() -> None:
    load_dotenv(ROOT / ".env")
    CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    # The MCP server runs as a subprocess for the whole session and is shut down on exit.
    with MCPToolClient() as mcp, closing(sqlite3.connect(CHECKPOINT_PATH, check_same_thread=False)) as conn:
        graph = build_graph(build_llm(), mcp, SqliteSaver(conn))
        print("模拟交易助手（输入 quit 退出）")
        while True:
            question = input("> ").strip()
            if question.lower() in ("quit", "exit"):
                break
            if not question:
                continue
            config = {"configurable": {"thread_id": str(uuid.uuid4())}}
            result = graph.invoke(new_state(question), config)
            # The graph pauses at human_approval; resume it with the person's decision.
            while "__interrupt__" in result:
                decision = ask_approval(result["__interrupt__"][0].value)
                result = graph.invoke(Command(resume=decision), config)
            print(to_text(result["messages"][-1].content))


if __name__ == "__main__":
    main()
