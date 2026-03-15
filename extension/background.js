/**
 * Background script for decomp.me Bridge extension (Firefox).
 * WebSocket connection lives here to avoid page CSP restrictions.
 */

const api = typeof browser !== "undefined" ? browser : chrome;
const MCP_WS_URL = "ws://127.0.0.1:9400";
let ws = null;
let activeTabId = null;
let reconnectTimer = null;

function connect() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;

  console.log("[decomp-bridge] Connecting to", MCP_WS_URL);

  try {
    ws = new WebSocket(MCP_WS_URL);
  } catch (err) {
    console.error("[decomp-bridge] WebSocket create error:", err);
    scheduleReconnect();
    return;
  }

  ws.onopen = () => {
    console.log("[decomp-bridge] Connected to MCP bridge!");
    clearReconnectTimer();
    ws.send(JSON.stringify({ type: "bridge_ready" }));
    // Notify content script
    if (activeTabId) {
      api.tabs.sendMessage(activeTabId, { type: "bridge_status", connected: true }).catch(() => {});
    }
  };

  ws.onmessage = async (event) => {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch {
      return;
    }

    console.log("[decomp-bridge] MCP command:", msg.type);

    // Handle ping in background directly
    if (msg.type === "ping") {
      ws.send(JSON.stringify({
        type: "response",
        id: msg.id,
        data: { pong: true, activeTabId: activeTabId }
      }));
      return;
    }

    if (activeTabId) {
      try {
        const response = await api.tabs.sendMessage(activeTabId, msg);
        ws.send(JSON.stringify({ type: "response", id: msg.id, data: response }));
      } catch (err) {
        ws.send(JSON.stringify({
          type: "response",
          id: msg.id,
          error: `Content script error: ${err.message}`
        }));
      }
    } else {
      ws.send(JSON.stringify({
        type: "response",
        id: msg.id,
        error: "No active decomp.me tab found. Open a scratch page first."
      }));
    }
  };

  ws.onclose = (event) => {
    console.log("[decomp-bridge] WS closed, code:", event.code, "reason:", event.reason);
    ws = null;
    if (activeTabId) {
      api.tabs.sendMessage(activeTabId, { type: "bridge_status", connected: false }).catch(() => {});
    }
    scheduleReconnect();
  };

  ws.onerror = (event) => {
    console.error("[decomp-bridge] WS error:", event);
  };
}

function scheduleReconnect() {
  clearReconnectTimer();
  reconnectTimer = setTimeout(connect, 3000);
}

function clearReconnectTimer() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
}

// Track active decomp.me tabs
api.tabs.onActivated.addListener(async (activeInfo) => {
  try {
    const tab = await api.tabs.get(activeInfo.tabId);
    if (tab.url && (tab.url.includes("decomp.me") || tab.url.includes("localhost"))) {
      activeTabId = activeInfo.tabId;
    }
  } catch {}
});

api.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.url && (changeInfo.url.includes("decomp.me") || changeInfo.url.includes("localhost"))) {
    activeTabId = tabId;
  }
});

// Content script registers itself
api.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "content_ready" && sender.tab) {
    activeTabId = sender.tab.id;
    console.log("[decomp-bridge] Content script ready on tab", activeTabId);
    sendResponse({ ok: true });
  }
  if (msg.type === "event" && ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(msg));
  }
  return true;
});

console.log("[decomp-bridge] Background script loaded");
connect();
