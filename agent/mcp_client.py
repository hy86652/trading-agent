"""MCP Client: starts the tool server, discovers its tools, and routes tool calls to it (Task 4).

The graph's nodes are synchronous but the MCP SDK is asynchronous. The session
therefore lives on a background event loop in its own thread, and the public
methods block until the result is ready.
"""
import asyncio
import concurrent.futures
import json
import logging
import sys
import threading
from pathlib import Path
from typing import Any

from langchain_core.tools import StructuredTool
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent.observability import tracer

ROOT = Path(__file__).resolve().parent.parent
CALL_TIMEOUT_SECONDS = 60
logger = logging.getLogger(__name__)


class MCPToolClient:
    """Context manager: `with MCPToolClient() as mcp:` keeps the server process alive for the block."""

    def __init__(self) -> None:
        self._params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "mcp_server.server"],
            cwd=str(ROOT),
        )
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._session: ClientSession | None = None
        self._stop: asyncio.Event | None = None
        self._serve_task: concurrent.futures.Future | None = None

    def __enter__(self) -> "MCPToolClient":
        self._thread.start()
        ready: concurrent.futures.Future = concurrent.futures.Future()
        self._serve_task = asyncio.run_coroutine_threadsafe(self._serve(ready), self._loop)
        ready.result(timeout=CALL_TIMEOUT_SECONDS)
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self._loop.call_soon_threadsafe(self._stop.set)
        self._serve_task.result(timeout=CALL_TIMEOUT_SECONDS)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=CALL_TIMEOUT_SECONDS)

    async def _serve(self, ready: concurrent.futures.Future) -> None:
        """Hold the server process and session open in one task until __exit__ sets the stop event.

        The SDK's stdio transport uses task groups, which must be entered and exited in the same task.
        """
        self._stop = asyncio.Event()
        try:
            async with stdio_client(self._params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self._session = session
                    ready.set_result(None)
                    await self._stop.wait()
        except BaseException as exc:
            if not ready.done():
                ready.set_exception(exc)
            else:
                raise

    def _run(self, coro: Any) -> Any:
        """Run a coroutine on the background loop and wait for its result from the calling thread."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=CALL_TIMEOUT_SECONDS)

    def list_tools(self) -> list[Any]:
        """Send tools/list and return the server's tool descriptions (name, description, inputSchema)."""
        result = self._run(self._session.list_tools())
        logger.info("tools/list -> %s", [t.name for t in result.tools])
        return result.tools

    def langchain_tools(self) -> list[StructuredTool]:
        """Wrap each discovered tool so the LLM can be bound to it. The schema comes from the server."""
        return [self._wrap(tool) for tool in self.list_tools()]

    def _wrap(self, tool: Any) -> StructuredTool:
        name = tool.name

        def run(**kwargs: Any) -> dict[str, Any]:
            return self.call(name, kwargs)

        return StructuredTool(
            name=name,
            description=tool.description or "",
            args_schema=tool.inputSchema,
            func=run,
        )

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Send tools/call and return the tool's dict result. Server-side errors come back as {"error": ...}."""
        request = {"method": "tools/call", "params": {"name": name, "arguments": arguments}}
        with tracer().start_as_current_observation(name=f"mcp tools/call {name}", as_type="tool", input=request) as span:
            result = self._run(self._session.call_tool(name, arguments))
            text = "".join(block.text for block in result.content if hasattr(block, "text"))
            output = {"error": text} if result.isError else self._parse(text)
            span.update(output={"result": output, "isError": bool(result.isError)})
        return output

    @staticmethod
    def _parse(text: str) -> dict[str, Any]:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"result": text}
