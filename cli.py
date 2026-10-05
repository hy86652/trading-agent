"""Local terminal entry point. Each question is a fresh run (no memory yet; checkpoints come in Task 7)."""
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage

from agent.graph import build_graph

ROOT = Path(__file__).resolve().parent

# Model choice is pending the user's decision (Groq or Gemini). Changing it means editing this one function.
GROQ_MODEL = "llama-3.3-70b-versatile"  # not verified against the current Groq catalogue


def build_llm():
    """Create the chat model. langchain-groq reads GROQ_API_KEY from the environment."""
    from langchain_groq import ChatGroq

    return ChatGroq(model=GROQ_MODEL, temperature=0)


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
    graph = build_graph(build_llm())
    print("模拟交易助手（输入 quit 退出）")
    while True:
        question = input("> ").strip()
        if question.lower() in ("quit", "exit"):
            break
        if not question:
            continue
        result = graph.invoke(new_state(question))
        print(result["messages"][-1].content)


if __name__ == "__main__":
    main()
