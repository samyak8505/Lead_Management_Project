"""Model Context Protocol (MCP) Server package for LeadFlow."""
from .server import run_stdio_server, handle_jsonrpc_request

__all__ = ["run_stdio_server", "handle_jsonrpc_request"]
