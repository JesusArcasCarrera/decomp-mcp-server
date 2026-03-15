"""Bridge client that connects to the standalone bridge_server.py WebSocket.

The bridge_server.py runs permanently. This module connects to it as a client,
sends commands, and receives responses routed through the browser extension.
"""

import asyncio
import json
import logging
import os
import uuid

logger = logging.getLogger("decomp-bridge")

BRIDGE_URL = os.environ.get("DECOMP_BRIDGE_URL", "ws://127.0.0.1:9400")


class BridgeError(Exception):
    pass


class BridgeClient:
    """Connects to the standalone bridge server to send commands to the browser."""

    def __init__(self, url: str = BRIDGE_URL):
        self.url = url
        self._ws = None
        self._pending: dict[str, asyncio.Future] = {}
        self._listen_task = None

    async def connect(self):
        """Connect to the bridge server."""
        if self._ws is not None:
            return

        try:
            import websockets.asyncio.client
        except ImportError:
            raise BridgeError("websockets not installed. Run: pip install websockets")

        try:
            self._ws = await websockets.asyncio.client.connect(self.url)
            self._listen_task = asyncio.create_task(self._listen())
            logger.info("Connected to bridge server at %s", self.url)
        except Exception as e:
            self._ws = None
            raise BridgeError(
                f"Cannot connect to bridge server at {self.url}: {e}. "
                "Make sure bridge_server.py is running: uv run python bridge_server.py"
            )

    async def _listen(self):
        """Listen for responses from the bridge server."""
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                if msg.get("type") == "response":
                    req_id = msg.get("id")
                    if req_id and req_id in self._pending:
                        self._pending.pop(req_id).set_result(msg)
        except Exception:
            pass
        finally:
            self._ws = None
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(BridgeError("Bridge connection lost"))
            self._pending.clear()

    @property
    def is_connected(self) -> bool:
        return self._ws is not None

    async def send_command(self, command: dict, timeout: float = 30.0) -> dict:
        """Send a command and wait for the response."""
        if not self.is_connected:
            await self.connect()

        req_id = uuid.uuid4().hex[:8]
        command["id"] = req_id

        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = fut

        await self._ws.send(json.dumps(command))

        try:
            msg = await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise BridgeError(f"Timeout after {timeout}s: {command.get('type')}")

        if "error" in msg:
            raise BridgeError(msg["error"])
        return msg.get("data", {})

    async def close(self):
        if self._ws:
            await self._ws.close()
            self._ws = None


# Singleton
bridge = BridgeClient()


# ─── High-level commands ───

async def bridge_ping() -> dict:
    return await bridge.send_command({"type": "ping"})


async def bridge_get_scratch_info() -> dict:
    return await bridge.send_command({"type": "get_scratch_info"})


async def bridge_get_source() -> str:
    result = await bridge.send_command({"type": "get_source"})
    return result.get("source", "")


async def bridge_set_source(code: str) -> dict:
    return await bridge.send_command({"type": "set_source", "code": code})


async def bridge_get_context() -> str:
    result = await bridge.send_command({"type": "get_context"})
    return result.get("context", "")


async def bridge_set_context(code: str) -> dict:
    return await bridge.send_command({"type": "set_context", "code": code})


async def bridge_compile(timeout: int = 30000) -> dict:
    import time
    t0 = time.monotonic()
    result = await bridge.send_command(
        {"type": "compile", "timeout": timeout},
        timeout=max(timeout / 1000 + 5, 35),
    )
    t1 = time.monotonic()
    logger.info("bridge_compile total: %.0fms", (t1 - t0) * 1000)
    return result


async def bridge_get_diff() -> dict:
    return await bridge.send_command({"type": "get_diff"})


async def bridge_get_compiler_output() -> str:
    result = await bridge.send_command({"type": "get_compiler_output"})
    return result.get("output", "")


async def bridge_get_score() -> dict:
    return await bridge.send_command({"type": "get_score"})


async def bridge_set_compiler_opts(
    compiler: str | None = None,
    flags: str | None = None,
    preset: str | None = None,
) -> dict:
    cmd: dict = {"type": "set_compiler_opts"}
    if compiler is not None:
        cmd["compiler"] = compiler
    if flags is not None:
        cmd["flags"] = flags
    if preset is not None:
        cmd["preset"] = preset
    return await bridge.send_command(cmd)


async def bridge_get_compiler_opts() -> dict:
    return await bridge.send_command({"type": "get_compiler_opts"})


async def bridge_navigate(url: str) -> dict:
    return await bridge.send_command({"type": "navigate", "url": url})
