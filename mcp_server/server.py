"""MCP Server exposing the four trading tools (Task 4).

The agent starts this file as a subprocess and talks to it over stdio. Stdout
carries the protocol, so nothing in this process may print to stdout.

Run with `python -m mcp_server.server` from the project root.
"""
from mcp.server.fastmcp import FastMCP

import tools

mcp = FastMCP("trading-tools")

# The server exposes all four tools. Which ones the LLM may call is decided by the agent, not here.
mcp.add_tool(tools.get_price)
mcp.add_tool(tools.get_indicators)
mcp.add_tool(tools.get_portfolio)
mcp.add_tool(tools.place_order)


if __name__ == "__main__":
    mcp.run(transport="stdio")
