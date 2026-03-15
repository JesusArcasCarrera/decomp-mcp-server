#!/usr/bin/env python3
"""Standalone WebSocket relay between the MCP server and the Firefox extension.

WHY THIS EXISTS
---------------
decomp.me sits behind Cloudflare, which blocks direct HTTP automation regardless
of cookies or user-agent tricks. Instead of fighting Cloudflare, we control the
browser from the inside via a Firefox extension that has a real authenticated
session. This relay server is the glue between Claude's MCP tools and that
extension.

CONNECTION MODEL
----------------
Two types of clients connect to this server on ws://127.0.0.1:9400:

  1. Browser extension — identified by its first message: {"type": "bridge_ready"}
     - There is at most ONE extension connection at a time.
     - It receives commands and sends back responses.

  2. MCP clients (server.py) — any other first message is treated as a command.
     - Multiple MCP clients can connect simultaneously (parallel Claude agents).
     - Each command carries a unique "id" so the response can be routed back.

MESSAGE FLOW
------------
  MCP client  →  bridge_server  →  Firefox extension  →  decomp.me DOM
  MCP client  ←  bridge_server  ←  Firefox extension

Usage:
    uv run python bridge_server.py
"""

import asyncio
import json
import logging
import signal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [bridge] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("bridge")

PORT = 9400

# The single active browser extension WebSocket (None when extension is not connected)
extension_ws = None
# All currently connected MCP client WebSockets (supports multiple parallel agents)
mcp_clients = set()
# Maps request id → MCP WebSocket so we can route the extension's response back
pending_requests = {}


async def handler(ws):
    """Handle a new WebSocket connection — either from the extension or an MCP client."""
    global extension_ws

    # The first message determines the connection type.
    # Extension sends {"type": "bridge_ready"}; anything else is an MCP command.
    try:
        first = await asyncio.wait_for(ws.recv(), timeout=5)
        msg = json.loads(first)
    except Exception:
        await ws.close()
        return

    if msg.get("type") == "bridge_ready":
        # This is the browser extension
        extension_ws = ws
        log.info("Browser extension connected")

        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                # Response from extension -> route back to the MCP client that asked
                if msg.get("type") == "response":
                    req_id = msg.get("id")
                    if req_id and req_id in pending_requests:
                        mcp_ws = pending_requests.pop(req_id)
                        try:
                            await mcp_ws.send(raw)
                        except Exception:
                            pass
                    continue

                if msg.get("type") == "event":
                    log.info("Extension event: %s", msg.get("event"))
                    continue
        except Exception as e:
            log.info("Extension disconnected: %s", e)
        finally:
            extension_ws = None
            log.info("Extension disconnected, waiting for reconnect...")
    else:
        # This is an MCP client
        mcp_clients.add(ws)
        log.info("MCP client connected")

        # Process the first message
        await handle_mcp_command(ws, msg)

        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                await handle_mcp_command(ws, msg)
        except Exception:
            pass
        finally:
            mcp_clients.discard(ws)
            log.info("MCP client disconnected")


async def handle_mcp_command(mcp_ws, msg):
    """Forward a command from MCP client to the browser extension."""
    req_id = msg.get("id")

    if not extension_ws:
        error = json.dumps({
            "type": "response",
            "id": req_id,
            "error": "Browser extension not connected. Open a decomp.me scratch page."
        })
        await mcp_ws.send(error)
        return

    # Remember which MCP client sent this request
    if req_id:
        pending_requests[req_id] = mcp_ws

    # Forward to extension
    try:
        await extension_ws.send(json.dumps(msg))
    except Exception as e:
        if req_id:
            pending_requests.pop(req_id, None)
        error = json.dumps({
            "type": "response",
            "id": req_id,
            "error": f"Failed to send to extension: {e}"
        })
        await mcp_ws.send(error)


async def main():
    import websockets.asyncio.server

    async with websockets.asyncio.server.serve(handler, "127.0.0.1", PORT) as server:
        log.info("Bridge server on ws://127.0.0.1:%d", PORT)
        log.info("Waiting for connections...")

        stop = asyncio.Event()
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)

        await stop.wait()
        log.info("Shutting down")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
