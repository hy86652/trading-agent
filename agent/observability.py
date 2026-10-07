"""Langfuse setup shared by the terminal and the API (Task 8).

Without both keys, tracing is switched off. Otherwise the SDK keeps trying to export
spans with empty credentials and logs 401 errors while the agent runs.
"""
import os


def disable_tracing_without_keys() -> None:
    """Call after loading .env and before the first Langfuse client is used."""
    if not (os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")):
        os.environ["LANGFUSE_TRACING_ENABLED"] = "false"
