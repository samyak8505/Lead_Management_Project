"""Model Context Protocol (MCP) Server for LeadFlow.

Exposes read-only CRM inspection, contact search, and pipeline statistics
over the standard JSON-RPC 2.0 stdio protocol for AI agents (Claude Desktop, Cursor, etc.).
"""
from __future__ import annotations
import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Optional

from leadflow.crm import SQLiteCRM
from leadflow.tools.registry import build_registry, ToolRegistry
from leadflow.workflow.extract import extract
from leadflow.workflow.scoring import score_lead

log = logging.getLogger("leadflow.mcp")

SERVER_NAME = "leadflow-crm-server"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2024-11-05"


def get_mcp_tools(registry: ToolRegistry, crm: SQLiteCRM) -> list[dict]:
    """Return tool schemas in standard MCP format."""
    mcp_tools = []
    
    # 1. Adapt tools from the read-only registry
    for spec in registry.specs():
        mcp_tools.append({
            "name": spec["name"],
            "description": spec["description"],
            "inputSchema": spec["parameters"],
        })
        
    # 2. Add high-level pipeline stats tool
    mcp_tools.append({
        "name": "get_crm_stats",
        "description": "Get high-level summary counts of contacts, companies, deals, and interactions in the CRM.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    })

    # 3. Add instant lead scoring tool
    mcp_tools.append({
        "name": "score_lead_inquiry",
        "description": "Analyze an inbound lead message and compute qualification score (0-100), tier (hot/warm/cold), and buying signals.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "raw_text": {
                    "type": "string",
                    "description": "Raw email body or form inquiry text to analyze"
                },
                "channel": {
                    "type": "string",
                    "enum": ["email", "web_form"],
                    "description": "Inbound communication channel (default: email)"
                }
            },
            "required": ["raw_text"]
        },
    })
    
    return mcp_tools


def execute_mcp_tool(
    name: str,
    args: dict,
    registry: ToolRegistry,
    crm: SQLiteCRM,
) -> dict:
    """Execute tool and format return value according to MCP specification."""
    try:
        if name == "get_crm_stats":
            stats = crm.stats()
            return {
                "content": [{"type": "text", "text": json.dumps(stats, indent=2)}],
                "isError": False,
            }
        
        elif name == "score_lead_inquiry":
            raw_text = args.get("raw_text", "")
            channel = args.get("channel", "email")
            extracted = extract(raw_text, channel)
            scored = score_lead(raw_text, extracted.data, crm=crm)
            res = {
                "score": scored.score,
                "tier": scored.tier,
                "reasons": scored.reasons,
                "rationale": scored.rationale,
                "extracted_fields": extracted.data,
            }
            return {
                "content": [{"type": "text", "text": json.dumps(res, indent=2)}],
                "isError": False,
            }
            
        else:
            # Delegate to read-only ToolRegistry
            res = registry.call(name, args)
            if not res.ok:
                return {
                    "content": [{"type": "text", "text": json.dumps({
                        "error_type": res.error_type,
                        "error": res.error,
                    })}],
                    "isError": True,
                }
            return {
                "content": [{"type": "text", "text": json.dumps(res.data, indent=2, default=str)}],
                "isError": False,
            }
    except Exception as e:
        return {
            "content": [{"type": "text", "text": f"Error executing {name}: {str(e)}"}],
            "isError": True,
        }


def handle_jsonrpc_request(req: dict, crm: SQLiteCRM, registry: ToolRegistry) -> Optional[dict]:
    """Handle a single MCP JSON-RPC 2.0 message."""
    method = req.get("method")
    req_id = req.get("id")
    params = req.get("params", {})

    # Notifications don't require responses
    if method == "notifications/initialized":
        return None

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {
                    "tools": {
                        "listChanged": False,
                    }
                },
                "serverInfo": {
                    "name": SERVER_NAME,
                    "version": SERVER_VERSION,
                },
            },
        }

    elif method == "ping":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {},
        }

    elif method == "tools/list":
        tools = get_mcp_tools(registry, crm)
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": tools,
            },
        }

    elif method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments", {})
        result = execute_mcp_tool(tool_name, tool_args, registry, crm)
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": result,
        }

    else:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": -32601,
                "message": f"Method '{method}' not found",
            },
        }


def run_stdio_server(db_path: str = "data/crm.db") -> None:
    """Run MCP server in standard input/output loop."""
    crm = SQLiteCRM(db_path)
    registry = build_registry(crm, actor="mcp-client", mode="read")
    
    # Write diagnostic logging to stderr to prevent corrupting stdout JSON-RPC
    sys.stderr.write(f"[LeadFlow MCP] Server started on stdio with CRM database: {db_path}\n")
    sys.stderr.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            res = handle_jsonrpc_request(req, crm, registry)
            if res is not None:
                sys.stdout.write(json.dumps(res) + "\n")
                sys.stdout.flush()
        except json.JSONDecodeError:
            err_res = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32700,
                    "message": "Parse error: Invalid JSON",
                },
            }
            sys.stdout.write(json.dumps(err_res) + "\n")
            sys.stdout.flush()
        except Exception as e:
            err_res = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32603,
                    "message": f"Internal error: {str(e)}",
                },
            }
            sys.stdout.write(json.dumps(err_res) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LeadFlow MCP Server")
    parser.add_argument("--db", default="data/crm.db", help="Path to SQLite CRM database")
    args = parser.parse_args()
    run_stdio_server(db_path=args.db)
