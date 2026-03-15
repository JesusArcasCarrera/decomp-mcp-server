# Firefox Extension Design

## Overview

A Firefox WebExtension (Manifest V2) that bridges the MCP server to the decomp.me page running in the browser. The extension handles auth and DOM interaction natively, bypassing Cloudflare.

## Files

| File | Role |
|------|------|
| `manifest.json` | Extension manifest (MV2, Firefox) |
| `background.js` | Service worker: WebSocket connection to bridge_server |
| `content.js` | Page script: DOM manipulation on scratch pages |

## Architecture

```
bridge_server.py
    │  WebSocket  ws://127.0.0.1:9400
    ▼
background.js  (persistent background page)
    │  browser.tabs.sendMessage(activeTabId, command)
    ▼
content.js  (injected into decomp.me/scratch/* and localhost/*)
    │  direct DOM access
    ▼
decomp.me page (React + CodeMirror 6)
```

## background.js

**Responsibilities:**
- Maintains the WebSocket connection to `bridge_server.py`
- Reconnects automatically every 3 seconds on disconnect
- Tracks `activeTabId`: the most recently focused decomp.me/localhost tab
- Routes incoming commands from bridge_server to the content script
- Sends responses back to bridge_server

**Connection flow:**
1. On load, calls `connect()`
2. On `ws.onopen`, sends `{ "type": "bridge_ready" }` to identify itself as the extension
3. On each `ws.onmessage`, forwards the command to `content.js` via `browser.tabs.sendMessage(activeTabId, msg)`
4. On response from content script, sends `{ "type": "response", "id": msg.id, "data": response }` back to bridge_server

**Tab tracking:**
- `tabs.onActivated` — updates `activeTabId` when user switches to a decomp.me tab
- `tabs.onUpdated` — updates `activeTabId` when a tab navigates to decomp.me
- `runtime.onMessage` with `type: "content_ready"` — content script registers itself on load

**Ping handling:** The background script handles `ping` commands directly without forwarding to content.js.

## content.js

Injected into all pages matching `https://decomp.me/scratch/*` and `http://localhost/*`.

Runs inside a Firefox content script sandbox — needs `wrappedJSObject` to access page-level JS objects (e.g. CodeMirror instances).

### Commands handled

| Command | Description |
|---------|-------------|
| `get_scratch_info` | Returns slug, name, platform, score |
| `get_source` | Returns source code from CodeMirror editor |
| `set_source` | Writes to source CodeMirror editor |
| `get_context` | Returns context panel code |
| `set_context` | Writes to context CodeMirror editor |
| `compile` | Clicks the Compile button, waits for result |
| `get_diff` | Reads assembly diff rows from DOM |
| `get_compiler_output` | Reads compiler error/output text |
| `get_score` | Reads match % from progress bar or badge |
| `get_compiler_opts` | Reads preset, compiler, and flags from the UI |
| `set_compiler_opts` | Sets preset, compiler, and/or flags in the UI |
| `navigate` | Changes `window.location.href` |
| `bridge_status` | Updates the status indicator dot color |
| `ping` | Returns `{ pong: true, slug, url }` |

### CodeMirror access

CodeMirror 6 stores the editor view on the DOM node under `.cmView.view`. Firefox content scripts can't access page-level JS objects directly — the `wrappedJSObject` property provides the unwrapped page object:

```js
function getCMView(cmContent) {
  const cmEditor = cmContent.closest(".cm-editor");
  const raw = cmEditor.wrappedJSObject || cmEditor;
  return raw.cmView?.view || null;
}
```

**Primary write method:** `view.dispatch({ changes: { from: 0, to: doc.length, insert: text } })`

**Fallback write method:** Focus + select all + `document.execCommand("insertText", false, text)` — used when `wrappedJSObject` is unavailable.

**Editor selectors:**
- Source: `.Tabs_active__vocGQ .Scratch_editor__aS9Xz:not(.Scratch_context__MuSV9 *) .cm-content`
- Context: `.Scratch_context__MuSV9 .cm-content`

Before reading/writing, the correct tab is activated with `clickTab("source")` or `clickTab("context")` to ensure the editor is mounted.

### React-controlled inputs

The compiler options UI is controlled by React. Setting `.value` directly doesn't trigger React's state update. The workaround uses the native property setter:

```js
function setReactValue(el, value) {
  const proto = el.tagName === "SELECT"
    ? window.HTMLSelectElement.prototype
    : window.HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
  setter.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
}
```

**Compiler UI selectors:**
- Preset select: `.CompilerOpts_preset__i0L4x select`
- Compiler select: `.CompilerOpts_compilerSelect__XKSfh select`
- Flags text input: `input.CompilerOpts_textbox__QZm58` (first match)

### Compilation detection

`waitForCompilation()` polls every 300ms until:
- The progress bar `data-state` attribute is no longer `"loading"`, OR
- The score text changes from its pre-compile value, OR
- The compiler output changes, OR
- The timeout is reached

Note: Firefox blocks overriding `window.fetch` in content scripts, so fetch interception is not used. Detection is purely DOM-based.

### Status indicator

A small colored dot fixed to the bottom-right corner:
- Gray: disconnected from bridge
- Green: connected to bridge

Clicking it shows an alert with the connection status and current slug.

## manifest.json

Manifest V2 (required for Firefox compatibility — MV3 service workers behave differently).

```json
{
  "manifest_version": 2,
  "permissions": ["activeTab", "tabs", "http://localhost/*", "https://decomp.me/*"],
  "content_scripts": [{
    "matches": ["https://decomp.me/scratch/*", "http://localhost/*"],
    "js": ["content.js"],
    "run_at": "document_idle"
  }],
  "background": { "scripts": ["background.js"] },
  "content_security_policy": "script-src 'self'; object-src 'self'; connect-src 'self' ws://127.0.0.1:9400 ws://localhost:9400;"
}
```

The CSP `connect-src` must explicitly allow the WebSocket URL or the connection will be blocked.

## Loading the extension

1. Open `about:debugging` in Firefox
2. Click **This Firefox**
3. Click **Load Temporary Add-on...**
4. Select `extension/manifest.json`

The extension persists until Firefox is restarted. To reload after code changes, click **Reload** on the extension entry in `about:debugging`.

## Prerequisites for operation

All three must be running/open simultaneously:

1. `bridge_server.py` — `uv run python bridge_server.py`
2. Firefox extension — loaded in `about:debugging`
3. A decomp.me scratch page open in Firefox
