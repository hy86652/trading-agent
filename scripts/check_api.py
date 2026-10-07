"""Offline check of the HTTP API with a scripted model and a temporary checkpoint file. No API key needed.

The MCP server is real, so tool calls are real. The approval check changes the simulated account,
so portfolio.json is restored afterwards.

Run from the project root with: python -m scripts.check_api
"""
import json
import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from api.main import create_app

PORTFOLIO_PATH = Path(__file__).resolve().parent.parent / "data" / "portfolio.json"


class ScriptedLLM:
    def __init__(self, replies: list[AIMessage]) -> None:
        self._replies = list(replies)

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        return self._replies.pop(0)


def order(quantity: int) -> AIMessage:
    return AIMessage(content="", tool_calls=[
        {"name": "place_order", "args": {"ticker": "AAPL", "side": "buy", "quantity": quantity}, "id": f"o{quantity}"},
    ])


def main() -> None:
    # The app loads .env on startup, which may hold real Langfuse keys. Offline checks must not send traces.
    os.environ["LANGFUSE_TRACING_ENABLED"] = "false"
    os.environ["API_TOKEN"] = "test-token"
    replies = [
        AIMessage(content="The AAPL price has been checked."),          # /webhook: text answer
        order(1),                                                      # /webhook: order waits for approval
        AIMessage(content="OK, the order was cancelled."),              # /approve reject
        order(1),                                                      # /webhook: order waits again
        AIMessage(content="Bought 1 share of AAPL."),                   # /approve approve
    ]
    original = PORTFOLIO_PATH.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(llm_factory=lambda: ScriptedLLM(replies), checkpoint_path=Path(tmp) / "checkpoints.db")
        try:
            PORTFOLIO_PATH.write_text(json.dumps({"cash": 100000.0, "positions": {}}), encoding="utf-8")
            # Without the token, the endpoints refuse; /health stays open for the platform's health check.
            anonymous = TestClient(app)  # not used as a context manager, so no lifespan is needed to reach the auth check
            assert anonymous.post("/webhook", json={"question": "hi"}).status_code == 401
            assert anonymous.post("/approve", json={"thread_id": "t", "decision": "approve"}).status_code == 401
            wrong = TestClient(app, headers={"X-API-Key": "wrong"})
            assert wrong.post("/webhook", json={"question": "hi"}).status_code == 401

            with TestClient(app) as client:
                assert client.get("/health").json() == {"status": "ok"}
                client.headers.update({"X-API-Key": "test-token"})

                first = client.post("/webhook", json={"question": "What is the AAPL price?"}).json()
                assert first["status"] == "completed" and first["answer"] == "The AAPL price has been checked.", first
                thread = first["thread_id"]

                pending = client.post("/webhook", json={"question": "Buy 1 share of AAPL", "thread_id": thread}).json()
                assert pending["status"] == "pending_approval", pending
                assert pending["order"] == {"ticker": "AAPL", "side": "buy", "quantity": 1}, pending
                assert pending["amount"] > 0

                blocked = client.post("/webhook", json={"question": "Ask again", "thread_id": thread})
                assert blocked.status_code == 409, blocked.status_code

                rejected = client.post("/approve", json={"thread_id": thread, "decision": "reject"}).json()
                assert rejected["status"] == "completed" and rejected["answer"] == "OK, the order was cancelled.", rejected
                assert json.loads(PORTFOLIO_PATH.read_text(encoding="utf-8"))["positions"] == {}

                nothing = client.post("/approve", json={"thread_id": thread, "decision": "approve"})
                assert nothing.status_code == 409, nothing.status_code

                assert client.post("/approve", json={"thread_id": thread, "decision": "maybe"}).status_code == 422

                again = client.post("/webhook", json={"question": "Buy 1 share of AAPL again", "thread_id": thread}).json()
                assert again["status"] == "pending_approval", again
                done = client.post("/approve", json={"thread_id": thread, "decision": "approve"}).json()
                assert done["status"] == "completed" and done["answer"] == "Bought 1 share of AAPL.", done
                assert json.loads(PORTFOLIO_PATH.read_text(encoding="utf-8"))["positions"] == {"AAPL": 1}
        finally:
            PORTFOLIO_PATH.write_text(original, encoding="utf-8")
    print("api checks passed")


if __name__ == "__main__":
    main()
