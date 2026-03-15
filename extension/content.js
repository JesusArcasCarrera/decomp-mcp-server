/**
 * Content script for decomp.me Bridge.
 *
 * Runs inside the decomp.me scratch page (injected by the extension).
 * Receives commands from background.js via browser.runtime.sendMessage and
 * manipulates the page DOM to read/write code and trigger compilation.
 *
 * WHY DOM MANIPULATION INSTEAD OF FETCH INTERCEPTION
 * ---------------------------------------------------
 * Firefox content scripts cannot override window.fetch (the page's own copy
 * runs in a different JS realm). Instead of intercepting network calls, we
 * drive the UI directly: write into CodeMirror, click the Compile button,
 * and detect when compilation finishes by watching DOM mutations.
 *
 * CODEMIRROR ACCESS IN FIREFOX
 * ---------------------------------------------------
 * CodeMirror 6 stores its EditorView on the DOM element as a JS property.
 * Firefox content scripts run in a sandbox and cannot access page-JS objects
 * directly — that is what `wrappedJSObject` is for. We use it to reach the
 * `.cmView.view` EditorView and call `dispatch()` to replace the document.
 * If that fails (e.g., the sandbox blocks it), we fall back to execCommand.
 */

(function () {
  "use strict";

  const api = typeof browser !== "undefined" ? browser : chrome;

  // ─── Helpers ───

  /** Access CodeMirror view via wrappedJSObject (Firefox content script sandbox bypass) */
  function getCMView(cmContent) {
    if (!cmContent) return null;
    const cmEditor = cmContent.closest(".cm-editor");
    if (!cmEditor) return null;
    // Firefox content scripts need wrappedJSObject to access page JS objects
    const raw = cmEditor.wrappedJSObject || cmEditor;
    return raw.cmView?.view || null;
  }

  function getSourceView() {
    // Source editor is inside the active tab panel, NOT inside Scratch_context
    const el = document.querySelector(".Tabs_active__vocGQ .Scratch_editor__aS9Xz:not(.Scratch_context__MuSV9 .Scratch_editor__aS9Xz) .cm-content")
      || document.querySelector(".Tabs_active__vocGQ .cm-content");
    return getCMView(el);
  }

  function getContextView() {
    const el = document.querySelector(".Scratch_context__MuSV9 .cm-content");
    return getCMView(el);
  }

  function setCMText(view, text) {
    if (!view) return false;
    try {
      view.dispatch({
        changes: { from: 0, to: view.state.doc.length, insert: text },
      });
      return true;
    } catch {
      return false;
    }
  }

  /** Write text to a CodeMirror editor by selecting all + inserting via InputEvent */
  function setCMTextViaPage(cmContentSelector, text) {
    try {
      const cmContent = document.querySelector(cmContentSelector);
      if (!cmContent) {
        console.log("[decomp-bridge] setCMText: selector not found:", cmContentSelector);
        return false;
      }

      // Focus the editor
      cmContent.focus();

      // Select all existing content
      const sel = window.getSelection();
      const range = document.createRange();
      range.selectNodeContents(cmContent);
      sel.removeAllRanges();
      sel.addRange(range);

      // Use execCommand to replace (works with contenteditable)
      document.execCommand("insertText", false, text);

      console.log("[decomp-bridge] setCMText: wrote via execCommand");
      return true;
    } catch (e) {
      console.error("[decomp-bridge] setCMTextViaPage error:", e);
      return false;
    }
  }

  function getCMText(view) {
    if (!view) return "";
    try {
      return view.state.doc.toString();
    } catch {
      return "";
    }
  }

  /** Fallback: read text directly from CodeMirror DOM lines */
  function readCMFromDOM(containerSelector) {
    const container = document.querySelector(containerSelector);
    if (!container) return "";
    const cmContent = container.querySelector(".cm-content");
    if (!cmContent) return "";
    const lines = cmContent.querySelectorAll(".cm-line");
    return Array.from(lines).map(l => l.textContent).join("\n");
  }

  /** Read source from the active tab's editor */
  function readActiveEditorDOM() {
    const active = document.querySelector(".Tabs_active__vocGQ .cm-content");
    if (!active) return "";
    const lines = active.querySelectorAll(".cm-line");
    return Array.from(lines).map(l => l.textContent).join("\n");
  }

  /**
   * Set value on a React-controlled input or select and fire change/input events.
   *
   * React intercepts the native setter so a simple `el.value = x` is silently
   * ignored. We have to call the *original* prototype setter (bypassing React's
   * override) and then dispatch synthetic events so React's onChange handler
   * picks up the new value.
   */
  function setReactValue(el, value) {
    if (!el) return false;
    const proto = el.tagName === "SELECT"
      ? window.HTMLSelectElement.prototype
      : window.HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    setter.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }

  function clickTab(label) {
    const tabs = document.querySelectorAll('[role="tab"]');
    for (const tab of tabs) {
      if (tab.textContent.trim().toLowerCase().startsWith(label.toLowerCase())) {
        tab.click();
        return true;
      }
    }
    return false;
  }

  /** Click the Options tab and wait for the compiler select to render */
  async function openCompilerOpts() {
    clickTab("options");
    const deadline = Date.now() + 1000;
    while (Date.now() < deadline) {
      if (document.querySelector(".CompilerOpts_compilerSelect__XKSfh select")) return true;
      await sleep(100);
    }
    return false;
  }

  function clickCompile() {
    const buttons = document.querySelectorAll("button");
    for (const btn of buttons) {
      if (btn.textContent.includes("Compile")) {
        btn.click();
        return true;
      }
    }
    return false;
  }

  function getSlug() {
    const m = window.location.pathname.match(/\/scratch\/([^/]+)/);
    return m ? m[1] : null;
  }

  function getScore() {
    const bar = document.querySelector("[role='progressbar']");
    if (bar) {
      const val = bar.getAttribute("aria-valuenow");
      const text = bar.getAttribute("aria-valuetext");
      if (val !== null) {
        return { percent: parseFloat(val), text: text || `${val}%` };
      }
    }
    const badge = document.querySelector(".ScoreBadge_badge__oXHwu");
    if (badge) {
      return { percent: -1, text: badge.textContent.trim() };
    }
    return { percent: -1, text: "unknown" };
  }

  function getDiff() {
    const rows = document.querySelectorAll(".Diff_row__zlCJp");
    const result = [];
    for (const row of rows) {
      const cells = row.querySelectorAll(".Diff_cell__FW8H_");
      const entry = { target: "", current: "" };
      if (cells[0]) entry.target = cells[0].textContent.trim();
      if (cells[1]) entry.current = cells[1].textContent.trim();
      result.push(entry);
    }
    return result;
  }

  function getCompilerOutput() {
    const code = document.querySelector(".Scratch_diffTab__zofDa code");
    if (code) return code.textContent.trim();
    return "";
  }

  function getScratchInfo() {
    return {
      slug: getSlug(),
      name: document.querySelector(".ScratchToolbar_name__Cwwn3")?.textContent?.trim() || "",
      platform: document.querySelector('.ScratchToolbar_iconNamePair__H7qs1 a[href*="/platform/"]')
        ?.getAttribute("href")?.replace("/platform/", "") || "",
      score: getScore(),
    };
  }

  function waitForCompilation(timeoutMs = 30000) {
    return new Promise((resolve) => {
      const startTime = Date.now();
      let settled = false;
      let mutationCount = 0;
      let debounceTimer = null;

      const finish = (timedOut) => {
        if (settled) return;
        settled = true;
        obs.disconnect();
        clearTimeout(timer);
        if (debounceTimer) clearTimeout(debounceTimer);
        console.log(`[decomp-bridge] waitForCompilation: ${Date.now() - startTime}ms, mutations=${mutationCount}, timedOut=${timedOut}`);
        resolve({
          score: getScore(),
          compiler_output: getCompilerOutput(),
          timed_out: timedOut,
        });
      };

      const timer = setTimeout(() => finish(true), timeoutMs);

      // After clicking Compile, React re-renders the diff/score/output areas.
      // Debounce: wait until mutations stop for 200ms, then resolve.
      const obs = new MutationObserver(() => {
        mutationCount++;
        if (debounceTimer) clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => finish(false), 200);
      });

      // Observe the entire scratch area for any child/attribute changes
      obs.observe(document.body, {
        subtree: true,
        childList: true,
        attributes: true,
        attributeFilter: ["data-state", "aria-valuenow", "aria-valuetext"],
      });
    });
  }

  // ─── Observe compilation results via XHR/fetch interception ───

  let lastCompilation = null;

  // Use a MutationObserver on the score badge instead of fetch interception
  // (Firefox blocks overwriting window.fetch in content scripts)
  const observer = new MutationObserver(() => {
    // Score badge or progress bar changed - compilation likely finished
  });
  const watchTarget = document.body;
  if (watchTarget) {
    observer.observe(watchTarget, { childList: true, subtree: true, attributes: true, attributeFilter: ["aria-valuenow", "data-state"] });
  }

  // ─── Message handler (from background script) ───

  api.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    handleCommand(msg).then(sendResponse).catch((err) => sendResponse({ error: err.message }));
    return true;
  });

  async function handleCommand(msg) {
    switch (msg.type) {
      case "get_scratch_info":
        return getScratchInfo();

      case "get_source": {
        clickTab("source");
        await sleep(300);
        let src = getCMText(getSourceView());
        if (!src) src = readActiveEditorDOM();
        return { source: src };
      }

      case "set_source": {
        clickTab("source");
        await sleep(400);
        const view = getSourceView();
        if (view && setCMText(view, msg.code)) return { ok: true };
        // Fallback: write via execCommand to the first non-context cpp editor
        const editors = document.querySelectorAll('.cm-content[data-language="cpp"]');
        for (const ed of editors) {
          if (ed.closest(".Scratch_context__MuSV9")) continue;
          if (setCMTextViaPage('.cm-content[data-language="cpp"]:not(.Scratch_context__MuSV9 .cm-content)', msg.code)) return { ok: true };
        }
        return { error: "Could not write to source editor" };
      }

      case "get_context": {
        clickTab("context");
        await sleep(300);
        let ctx = getCMText(getContextView());
        if (!ctx) ctx = readCMFromDOM(".Scratch_context__MuSV9");
        return { context: ctx };
      }

      case "set_context": {
        clickTab("context");
        await sleep(400);
        const ctxSelector = ".Scratch_context__MuSV9 .cm-content";
        const ctxView = getContextView();
        if (ctxView && setCMText(ctxView, msg.code)) return { ok: true };
        const ok = setCMTextViaPage(ctxSelector, msg.code);
        return ok ? { ok: true } : { error: "Could not write to context editor" };
      }

      case "compile": {
        const t0 = Date.now();
        clickCompile();
        const result = await waitForCompilation(msg.timeout || 30000);
        const t1 = Date.now();
        console.log(`[decomp-bridge] compile: waitForCompilation=${t1 - t0}ms`);
        const t2 = Date.now();
        const diff = getDiff();
        const t3 = Date.now();
        console.log(`[decomp-bridge] compile: getDiff=${t3 - t2}ms, total=${t3 - t0}ms`);
        result.diff = diff;
        return result;
      }

      case "get_diff":
        return { diff: getDiff(), score: getScore() };

      case "get_compiler_output":
        return { output: getCompilerOutput() };

      case "get_score":
        return getScore();

      case "set_compiler_opts": {
        await openCompilerOpts();
        await sleep(200);
        const results = {};
        if (msg.preset !== undefined) {
          const presetSel = document.querySelector(".CompilerOpts_preset__i0L4x select");
          results.preset = setReactValue(presetSel, msg.preset);
          await sleep(200);
        }
        if (msg.compiler !== undefined) {
          const compilerSel = document.querySelector(".CompilerOpts_compilerSelect__XKSfh select");
          results.compiler = setReactValue(compilerSel, msg.compiler);
          await sleep(200);
        }
        if (msg.flags !== undefined) {
          const flagsInput = document.querySelector("input.CompilerOpts_textbox__QZm58");
          results.flags = setReactValue(flagsInput, msg.flags);
        }
        return { ok: true, results };
      }

      case "get_compiler_opts": {
        await openCompilerOpts();
        await sleep(200);
        const presetSel = document.querySelector(".CompilerOpts_preset__i0L4x select");
        const compilerSel = document.querySelector(".CompilerOpts_compilerSelect__XKSfh select");
        const flagsInput = document.querySelector("input.CompilerOpts_textbox__QZm58");
        return {
          preset: presetSel?.value || null,
          compiler: compilerSel?.value || null,
          flags: flagsInput?.value || null,
        };
      }

      case "get_last_compilation":
        return { error: "Direct API interception not available in Firefox" };

      case "navigate":
        if (msg.url) {
          window.location.href = msg.url;
          return { ok: true };
        }
        return { error: "No URL" };

      case "bridge_status":
        setStatus(msg.connected);
        return { ok: true };

      case "ping":
        return { pong: true, slug: getSlug(), url: window.location.href };

      default:
        return { error: `Unknown: ${msg.type}` };
    }
  }

  function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
  }

  // ─── Status indicator ───

  let statusDot = null;

  function createStatusIndicator() {
    statusDot = document.createElement("div");
    statusDot.id = "decomp-bridge-status";
    statusDot.title = "MCP Bridge: disconnected";
    Object.assign(statusDot.style, {
      position: "fixed",
      bottom: "12px",
      right: "12px",
      width: "14px",
      height: "14px",
      borderRadius: "50%",
      backgroundColor: "#666",
      border: "2px solid #333",
      zIndex: "99999",
      cursor: "pointer",
      transition: "background-color 0.3s",
      boxShadow: "0 0 4px rgba(0,0,0,0.5)",
    });
    statusDot.addEventListener("click", () => {
      alert(`decomp.me Bridge\n\nStatus: ${statusDot.title}\nSlug: ${getSlug()}`);
    });
    document.body.appendChild(statusDot);
  }

  function setStatus(connected) {
    if (!statusDot) return;
    if (connected) {
      statusDot.style.backgroundColor = "#22cc44";
      statusDot.style.boxShadow = "0 0 8px rgba(34,204,68,0.6)";
      statusDot.title = "MCP Bridge: connected";
    } else {
      statusDot.style.backgroundColor = "#666";
      statusDot.style.boxShadow = "0 0 4px rgba(0,0,0,0.5)";
      statusDot.title = "MCP Bridge: disconnected";
    }
  }

  // ─── Init ───
  if (window.location.pathname.includes("/scratch/")) {
    createStatusIndicator();
    api.runtime.sendMessage({ type: "content_ready" }).catch(() => {});
    console.log("[decomp-bridge] Content script loaded for", getSlug());
  }
})();
