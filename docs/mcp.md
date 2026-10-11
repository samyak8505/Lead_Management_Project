# LeadFlow Model Context Protocol (MCP) Server (Step 16)

## Overview

LeadFlow provides an official, zero-external-dependency Model Context Protocol (MCP) server adhering to the `2024-11-05` specification over JSON-RPC 2.0 stdio.

The MCP server exposes read-only CRM inspection tools, pipeline statistics, and lead qualification engines to AI assistants (such as Claude Desktop, Cursor, Antigravity, or custom agent runtimes) under strict least-privilege security boundaries.

---

## 1. Exposed MCP Tools

| Tool Name | Type | Description | Input Parameters |
|---|---|---|---|
| `search_contact` | Read | Search CRM contacts by email address or fuzzy name/company. | `{"email": "...", "name": "...", "company": "..."}` |
| `get_contact_details` | Read | Fetch deep contact profile including linked deals and last 5 interactions. | `{"contact_id": 123}` |
| `get_crm_stats` | Metric | Aggregate counts of contacts, companies, active deals, and interactions. | `{}` |
| `score_lead_inquiry` | Engine | Run LeadFlow qualification scoring and buying signal detection on raw text. | `{"raw_text": "...", "channel": "email"}` |

> **Security Note**: All write operations (`create_contact`, `create_deal`, `update_contact`) are completely excluded from the MCP tool catalog to guarantee zero unapproved mutations from external agents.

---

## 2. Configuration for AI Clients

### Claude Desktop Configuration
Add the following to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "leadflow-crm": {
      "command": "python",
      "args": [
        "-m",
        "leadflow.mcp.server",
        "--db",
        "c:/Users/SAMYAK/Downloads/leadflow-starter (1)/data/crm.db"
      ]
    }
  }
}
```

### Antigravity / Cursor MCP Configuration
In your project or workspace `.gemini/config/mcp_config.json` or Cursor Settings:

```json
{
  "mcpServers": {
    "leadflow": {
      "command": ".venv/Scripts/python.exe",
      "args": ["-m", "leadflow.mcp.server", "--db", "data/crm.db"]
    }
  }
}
```

---

## 3. Running from Command Line

To test the server interactively via stdio:

```bash
python -m leadflow.mcp.server --db data/crm.db
```

Sample JSON-RPC handshake:

```json
{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}}
```

Sample Response:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": "2024-11-05",
    "capabilities": {"tools": {"listChanged": false}},
    "serverInfo": {"name": "leadflow-crm-server", "version": "1.0.0"}
  }
}
```
