"""Settings and helpers shared by the terminal, the HTTP API and the evaluation scripts.

Keeping these here means the API and the scripts do not import the terminal entry point.
"""
import logging
import os
from pathlib import Path

from langchain_core.messages import HumanMessage

ROOT = Path(__file__).resolve().parent.parent
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


def configure_logging() -> None:
    """Log to stderr. Quiet by default; set LOG_LEVEL=INFO to see tools/list and tools/call."""
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "WARNING").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
