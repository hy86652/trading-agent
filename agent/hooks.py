"""Pre-tool hook: deterministic checks that run before an order is sent for approval (Task 5).

The LLM is non-deterministic, so the rules an order must meet live here as code.
A failed check returns a simulated HTTP 400 to the reasoner, which can correct itself.
Market and account data are read through the MCP client, the same path the agent uses for tools.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from langchain_core.messages import AIMessage, ToolMessage

from agent.mcp_client import MCPToolClient
from agent.state import AgentState

MAX_CONSECUTIVE_ERRORS = 3  # brief section 5: stop the turn after this many failed checks
MAX_ORDER_SHARE = 0.20  # one order may not exceed 20% of total account value (brief section 4)
MAX_TOOL_OUTPUT_CHARS = 4000  # generous for now: current tool outputs are short; lower it if outputs grow


def validate_order(args: dict[str, Any], mcp: MCPToolClient) -> tuple[str | None, float | None]:
    """Return (error, amount). error is None when the order passes; amount is the estimated cost."""
    ticker = args.get("ticker")
    side = args.get("side")
    quantity = args.get("quantity")

    if not isinstance(ticker, str):
        return "ticker must be a string", None
    price_info = mcp.call("get_price", {"ticker": ticker, "days": 1})
    if "error" in price_info:
        return f"ticker is not in the whitelist: {price_info['error']}", None
    price = price_info["bars"][0]["close"]

    if side not in ("buy", "sell"):
        return "side must be 'buy' or 'sell'", None
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        return "quantity must be a positive integer", None

    amount = round(price * quantity, 2)
    portfolio = mcp.call("get_portfolio", {})
    if side == "buy" and amount > portfolio["cash"]:
        return f"insufficient cash: order costs {amount}, cash is {portfolio['cash']}", amount
    if side == "sell":
        held = next((p["quantity"] for p in portfolio["positions"] if p["ticker"] == ticker.upper()), 0)
        if quantity > held:
            return f"insufficient position: hold {held}, tried to sell {quantity} (no shorting)", amount

    limit = round(portfolio["total_value"] * MAX_ORDER_SHARE, 2)
    if amount > limit:
        return f"order amount {amount} exceeds {MAX_ORDER_SHARE:.0%} of total assets (limit {limit})", amount
    return None, amount


def make_pre_hook(mcp: MCPToolClient) -> Callable[[AgentState], dict[str, Any]]:
    """Return the pre-hook node. It passes read-only calls through and checks place_order before approval."""

    def pre_hook(state: AgentState) -> dict[str, Any]:
        calls = state["messages"][-1].tool_calls
        orders = [c for c in calls if c["name"] == "place_order"]

        amount = None
        if orders and len(calls) > 1:
            error = "place_order must be the only tool call in a turn; resend it alone"
        elif orders:
            error, amount = validate_order(orders[0]["args"], mcp)
        else:
            error = None

        if error is None:
            # A passing check resets the consecutive-error counter.
            update: dict[str, Any] = {"error_count": 0, "approval": None, "pending_order": None}
            if orders:
                update["pending_order"] = {"call_id": orders[0]["id"], "args": orders[0]["args"], "amount": amount}
            return update

        messages = []
        for call in calls:
            if call["name"] == "place_order":
                payload = {"status": 400, "error": error}
            else:
                payload = {"status": 400, "error": "not executed: resend this call in a separate turn"}
            messages.append(
                ToolMessage(content=json.dumps(payload, ensure_ascii=False), tool_call_id=call["id"], name=call["name"])
            )
        count = state["error_count"] + 1
        if count >= MAX_CONSECUTIVE_ERRORS:
            messages.append(AIMessage(content="连续 3 次订单校验失败，已停止本次请求。请检查订单内容后再试。"))
        return {"messages": messages, "error_count": count, "pending_order": None}

    return pre_hook


def make_post_hook(log_path: Path) -> Callable[[AgentState], dict[str, Any]]:
    """Return the post-tool hook node. It runs on the results of the latest tool execution, before the reasoner sees them.

    It does three things: adds a short summary to a filled order, logs every tool call to a JSON Lines file,
    and truncates very long outputs so they cannot crowd out the context.
    """

    def post_hook(state: AgentState) -> dict[str, Any]:
        messages = state["messages"]
        last_ai = max(i for i, m in enumerate(messages) if isinstance(m, AIMessage))
        results = [m for m in messages[last_ai + 1:] if isinstance(m, ToolMessage)]
        calls = {c["id"]: c for c in messages[last_ai].tool_calls}

        processed = []
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as log:
            for result in results:
                call = calls.get(result.tool_call_id, {})
                output = json.loads(result.content)
                if result.name == "place_order" and output.get("status") == "filled":
                    output["summary"] = (
                        f"已{'买入' if output['side'] == 'buy' else '卖出'} {output['ticker']} × {output['quantity']} 股，"
                        f"成交价 {output['price']}，剩余现金 {output['cash']}"
                    )
                content = json.dumps(output, ensure_ascii=False)
                if len(content) > MAX_TOOL_OUTPUT_CHARS:
                    content = content[:MAX_TOOL_OUTPUT_CHARS] + f"...[已截断，原长度 {len(content)} 字符]"
                log.write(json.dumps({
                    "time": datetime.now(timezone.utc).isoformat(),
                    "tool": result.name,
                    "args": call.get("args"),
                    "error": "error" in output or output.get("status") in (400, "rejected"),
                    "output_chars": len(content),
                }, ensure_ascii=False) + "\n")
                processed.append(ToolMessage(content=content, tool_call_id=result.tool_call_id, name=result.name, id=result.id))
        return {"messages": processed}

    return post_hook
