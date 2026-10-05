"""Local terminal entry point. Each question is a fresh run (no memory yet; checkpoints come in Task 7)."""
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage

from agent.graph import build_graph
from agent.mcp_client import MCPToolClient

ROOT = Path(__file__).resolve().parent

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


def main() -> None:
    load_dotenv(ROOT / ".env")
    # The MCP server runs as a subprocess for the whole session and is shut down on exit.
    with MCPToolClient() as mcp:
        graph = build_graph(build_llm(), mcp)
        print("模拟交易助手（输入 quit 退出）")
        while True:
            question = input("> ").strip()
            if question.lower() in ("quit", "exit"):
                break
            if not question:
                continue
            result = graph.invoke(new_state(question))
            print(to_text(result["messages"][-1].content))


if __name__ == "__main__":
    main()
