"""HTTP entry point for the agent (Task 9).

POST /webhook   ask a question. Returns the answer, or "pending_approval" when an order needs a decision.
POST /approve   answer a pending order with "approve" or "reject".
GET  /health    liveness check.

Terminal approval does not work on a hosted server, so approval is a second request (brief section 10).
Requests are handled one at a time: the agent graph is not built for concurrent runs on one checkpoint store.

Run locally from the project root with: uvicorn api.main:app --port 8000
"""
import sqlite3
import threading
import uuid
from contextlib import asynccontextmanager, closing
from pathlib import Path
from typing import Any, Callable, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from pydantic import BaseModel, Field

from agent.graph import build_graph
from agent.mcp_client import MCPToolClient
from agent.nodes import to_text
from agent.observability import disable_tracing_without_keys
from agent.runtime import CHECKPOINT_PATH, ROOT, build_llm, configure_logging, question_input


class QuestionIn(BaseModel):
    question: str = Field(min_length=1)
    thread_id: str | None = None


class DecisionIn(BaseModel):
    thread_id: str
    decision: Literal["approve", "reject"]


class AgentService:
    """Wraps the compiled graph. One lock keeps runs and approvals from interleaving on the same checkpoint store."""

    def __init__(self, graph: Any) -> None:
        self.graph = graph
        self.lock = threading.Lock()

    def ask(self, question: str, thread_id: str | None) -> dict[str, Any]:
        thread_id = thread_id or str(uuid.uuid4())
        config = {"configurable": {"thread_id": thread_id}}
        with self.lock:
            snapshot = self.graph.get_state(config)
            if snapshot.next:
                raise HTTPException(409, "this thread is waiting for an approval; call /approve first")
            resuming = bool(snapshot.values)
            result = self.graph.invoke(question_input(question, resuming), config)
        return self._reply(thread_id, result)

    def approve(self, thread_id: str, decision: str) -> dict[str, Any]:
        config = {"configurable": {"thread_id": thread_id}}
        with self.lock:
            if not self.graph.get_state(config).next:
                raise HTTPException(409, "no order is waiting for approval on this thread")
            result = self.graph.invoke(Command(resume=decision), config)
        return self._reply(thread_id, result)

    @staticmethod
    def _reply(thread_id: str, result: dict[str, Any]) -> dict[str, Any]:
        if "__interrupt__" in result:
            payload = result["__interrupt__"][0].value
            return {
                "thread_id": thread_id,
                "status": "pending_approval",
                "order": payload["order"],
                "amount": payload["amount"],
            }
        return {"thread_id": thread_id, "status": "completed", "answer": to_text(result["messages"][-1].content)}


def create_app(llm_factory: Callable[[], Any] = build_llm, checkpoint_path: Path = CHECKPOINT_PATH) -> FastAPI:
    """Build the app. Tests pass a scripted model and a temporary checkpoint file; production uses the defaults."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        load_dotenv(ROOT / ".env")
        configure_logging()
        disable_tracing_without_keys()
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        with MCPToolClient() as mcp, closing(sqlite3.connect(checkpoint_path, check_same_thread=False)) as conn:
            app.state.service = AgentService(build_graph(llm_factory(), mcp, SqliteSaver(conn)))
            yield

    app = FastAPI(title="Simulated trading assistant", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhook")
    def webhook(body: QuestionIn, request: Request) -> dict[str, Any]:
        try:
            return request.app.state.service.ask(body.question, body.thread_id)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(503, f"agent call failed: {type(exc).__name__}") from exc

    @app.post("/approve")
    def approve(body: DecisionIn, request: Request) -> dict[str, Any]:
        try:
            return request.app.state.service.approve(body.thread_id, body.decision)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(503, f"agent call failed: {type(exc).__name__}") from exc

    return app


app = create_app()
