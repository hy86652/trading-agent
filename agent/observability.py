"""Langfuse setup shared by the terminal and the API (Task 8).

Without both keys, tracing is off and no Langfuse client is created at all. Creating one
without keys prints an authentication warning every time, and with keys the SDK keeps trying to
export spans with empty credentials and logs 401 errors while the agent runs.
"""
import os
from contextlib import contextmanager
from typing import Any, Iterator


def tracing_enabled() -> bool:
    """True only when both keys are set and tracing has not been switched off explicitly."""
    if os.getenv("LANGFUSE_TRACING_ENABLED", "true").lower() == "false":
        return False
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))


def disable_tracing_without_keys() -> None:
    """Call after loading .env and before the first traced call."""
    if not tracing_enabled():
        os.environ["LANGFUSE_TRACING_ENABLED"] = "false"


class _NoopObservation:
    def update(self, **kwargs: Any) -> None:
        pass


class _NoopTracer:
    @contextmanager
    def start_as_current_observation(self, **kwargs: Any) -> Iterator[_NoopObservation]:
        yield _NoopObservation()

    def flush(self) -> None:
        pass


def tracer() -> Any:
    """The Langfuse client when tracing is on, otherwise a no-op with the same methods the agent uses."""
    if not tracing_enabled():
        return _NOOP
    from langfuse import get_client

    return get_client()


_NOOP = _NoopTracer()
