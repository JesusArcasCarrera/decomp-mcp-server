# MCP Server Design

## Overview

The MCP server (`src/decomp_mcp/server.py`) exposes 14 tools to Claude via the Model Context Protocol over stdio. All interaction with decomp.me goes through the Firefox browser extension — there is no direct HTTP connection to decomp.me.

```
Claude (MCP client)
    │  stdio
    ▼
server.py  (MCP server)
    │  WebSocket  ws://127.0.0.1:9400
    ▼
bridge_server.py  (WebSocket relay, always running)
    │  WebSocket  ws://127.0.0.1:9400
    ▼
Firefox extension  (background.js)
    │  browser.tabs.sendMessage
    ▼
content.js  (running on the decomp.me scratch page)
    │  DOM manipulation
    ▼
decomp.me page
```

## Files

| File | Role |
|------|------|
| `src/decomp_mcp/server.py` | MCP server: tool definitions + handlers |
| `src/decomp_mcp/bridge.py` | Bridge client: WebSocket connection to bridge_server |
| `bridge_server.py` | Standalone WebSocket relay (run once, keep running) |
| `patterns/db.py` | SQLite patterns database |

## server.py

Entry point: `asyncio.run(main())`. Runs as an MCP stdio server.

Tool dispatch in `call_tool()`:
- `bridge_*` → `handle_bridge_command(name, arguments)`
- `decomp_claim_function` / `decomp_release_function` / `decomp_list_claims` → file-based claim handlers
- `decomp_complete_function` / `decomp_list_completed` → file-based completed handlers
- `decomp_search_patterns` / `decomp_save_pattern` → `patterns/db.py`

### Coordination State

Two JSON files managed with `fcntl` file locking for parallel-agent safety:

**Claims** (`/tmp/decomp_claims.json`):
```json
{
  "sub_0200BC54": {
    "agent_id": "agent-1",
    "timestamp": 1710000000.0
  }
}
```
Claims expire after `DECOMP_CLAIM_TIMEOUT` seconds (default: 3600). Stale entries are pruned on every `_load_claims()` call.

**Completed** (`/tmp/decomp_completed.json`):
```json
{
  "sub_0200BC54": {
    "match_percent": 100.0,
    "scratch_slug": "oXxpc",
    "committed": false,
    "notes": "",
    "timestamp": 1710000000.0
  }
}
```
Completed functions are permanent — they block other agents from claiming the function.

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DECOMP_CLAIMS_FILE` | `/tmp/decomp_claims.json` | Claims state file |
| `DECOMP_COMPLETED_FILE` | `/tmp/decomp_completed.json` | Completed functions file |
| `DECOMP_CLAIM_TIMEOUT` | `3600` | Claim TTL in seconds |
| `DECOMP_BRIDGE_URL` | `ws://127.0.0.1:9400` | Bridge server address |

## bridge.py

Singleton `BridgeClient` that connects lazily to `bridge_server.py` on first use.

- Maintains a persistent WebSocket connection
- Each command gets a random 8-hex `id`
- Pending futures keyed by id; resolved when the response arrives
- `_listen()` task runs in the background handling incoming responses
- On disconnect, all pending futures raise `BridgeError`

Command format sent to bridge_server:
```json
{ "type": "set_source", "id": "a3f2b1c0", "code": "void fn() {...}" }
```

Response received from bridge_server:
```json
{ "type": "response", "id": "a3f2b1c0", "data": { "ok": true } }
```

## bridge_server.py

Standalone process (`uv run python bridge_server.py`) that must be running before the MCP server or the extension connects.

Distinguishes two connection types by the first message:
- `{ "type": "bridge_ready" }` → this is the Firefox extension
- anything else → this is an MCP client

Routing: when an MCP client sends a command with `id`, the server stores `pending_requests[id] = mcp_ws`, forwards the command to `extension_ws`, and when the extension replies with `{ "type": "response", "id": ... }`, routes it back to the correct MCP client.

Only one extension connection is tracked at a time (`extension_ws` global). Multiple MCP clients are supported simultaneously (`mcp_clients` set).

## Patterns DB

SQLite at `patterns/patterns.db` (managed by `patterns/db.py`).

Schema: `platform`, `compiler`, `asm_pattern`, `c_code`, `match_score`, `scratch_url`, `notes`, `tags`, `created_at`.

Search uses `LIKE` substring matching on `asm_pattern`, exact match on `platform`/`compiler`, and substring on `tags`.
