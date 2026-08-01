/* Gemini Chrome Bridge -- content script.
 *
 * Runs inside gemini.google.com. Owns a WebSocket to the local relay
 * (ws://localhost:8111/ws), receives {type:"chat",...} jobs, drives the real UI
 * (new chat -> type prompt -> send -> scrape reply), and posts the answer back.
 *
 * Why the content script (not the service worker) owns the socket: MV3 service
 * workers are killed after ~30s idle; a content script lives as long as the tab.
 * ws://localhost from an https page is allowed (localhost is "potentially
 * trustworthy"), so no mixed-content block.
 *
 * Completion detection is deliberately language-agnostic: aria-labels here are
 * Arabic, so we rely on response-text STABILITY plus the disappearance of the
 * stop-generating control, not on English strings.
 */
(() => {
  "use strict";
  // Newest injected instance wins. The marker lives in the shared DOM because
  // isolated worlds (old vs freshly-injected extension instance) do not share
  // `window`. Old instances notice the marker changed and shut down; orphaned
  // instances (extension reloaded away) also lose chrome.runtime and shut down.
  const MY_GEN = Date.now() + "-" + Math.random().toString(36).slice(2, 8);
  document.documentElement.dataset.gcbGen = MY_GEN;
  let shuttingDown = false;
  // Server port (gemini-web-api default is 8100). Override via the extension's
  // storage: chrome.storage.local.set({port: 8100}).
  const DEFAULT_PORT = 8100;
  let PORT = DEFAULT_PORT;
  const LOG = (...a) => console.log("[GCB]", ...a);

  // ---- tiny async helpers ----
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  async function waitFor(pred, { timeout = 20000, interval = 200 } = {}) {
    const t0 = Date.now();
    for (;;) {
      const v = pred();
      if (v) return v;
      if (Date.now() - t0 > timeout) return null;
      await sleep(interval);
    }
  }
  const visible = (el) =>
    el && el.offsetParent !== null && el.getClientRects().length > 0;

  // ---- DOM locators (multiple strategies; first hit wins) ----
  function findComposer() {
    const cands = [
      'div.ql-editor[contenteditable="true"]',
      'rich-textarea div[contenteditable="true"]',
      '[aria-label="Enter a prompt for Gemini"]',
      'div[contenteditable="true"][role="textbox"]',
      'textarea',
    ];
    for (const sel of cands) {
      const el = [...document.querySelectorAll(sel)].find(visible);
      if (el) return el;
    }
    return null;
  }

  function findSendButton() {
    // The send button replaces the mic once text is present. Real Gemini uses
    // aria-label "Send message" with an "arrow_upward" icon (NOT "send").
    const byLabel = [...document.querySelectorAll(
      'button[aria-label*="Send" i],button[aria-label*="إرسال"],button[mattooltip*="Send" i]'
    )].find((b) => visible(b) && !b.disabled);
    if (byLabel) return byLabel;
    const byIcon = [...document.querySelectorAll("button")].find((b) => {
      if (!visible(b) || b.disabled) return false;
      const icon = b.querySelector("mat-icon");
      const fi = icon && (icon.getAttribute("fonticon") || icon.textContent || "");
      const f = fi && fi.trim().toLowerCase();
      return f === "send" || f === "arrow_upward";
    });
    return byIcon || null;
  }

  function findStopButton() {
    // Present only while generating.
    return [...document.querySelectorAll("button")].find((b) => {
      if (!visible(b)) return false;
      const icon = b.querySelector("mat-icon");
      const fi = icon && (icon.getAttribute("fonticon") || icon.textContent || "");
      const lab = (b.getAttribute("aria-label") || "") + (b.getAttribute("mattooltip") || "");
      return (
        (fi && fi.trim().toLowerCase() === "stop") ||
        /stop/i.test(lab) ||
        /إيقاف|ايقاف/.test(lab)
      );
    });
  }

  function responseNodes() {
    // Each model turn. Prefer specific containers, fall back progressively.
    for (const sel of [
      "message-content.model-response-text",
      ".model-response-text",
      "model-response",
      "message-content",
    ]) {
      const nodes = [...document.querySelectorAll(sel)].filter(visible);
      if (nodes.length) return nodes;
    }
    return [];
  }

  function findNewChatControl() {
    const a =
      document.querySelector('a[href="/app"]') ||
      document.querySelector('a[href="/"]');
    if (a && visible(a)) return a;
    return [...document.querySelectorAll("button,a")].find((b) => {
      const lab = (b.getAttribute("aria-label") || "") + " " + (b.textContent || "");
      return visible(b) && /new chat|محادثة جديدة/i.test(lab);
    });
  }

  // First-use onboarding ("Keep in mind ... Got it") blocks the whole UI on an
  // account's first Gemini visit -- jobs then look like eternal "did not render".
  // It is an informational acknowledgement, so dismiss it and log that we did.
  function dismissOnboarding() {
    const btn = [...document.querySelectorAll("button")].find((b) => {
      const t = (b.textContent || "").trim();
      return visible(b) && /^(Got it|حسنًا|حسنا|فهمت)$/i.test(t);
    });
    if (btn) {
      LOG("dismissing first-use onboarding dialog");
      btn.click();
      return true;
    }
    return false;
  }

  // Per-account daily media quota exhaustion answers with a text like
  // "I can create more images as soon as your limit resets" and no media --
  // detect it and fail FAST instead of burning the full media timeout.
  function quotaLimitText() {
    const nodes = responseNodes();
    const text = nodes.length ? (nodes[nodes.length - 1].innerText || "") : "";
    return /limit resets|generation limit|reached your limit|الحد الأقصى/i.test(text)
      ? text.trim().slice(0, 160)
      : null;
  }

  // ---- actions ----
  async function startNewChat() {
    const ctl = findNewChatControl();
    if (!ctl) {
      LOG("no new-chat control; sending in current thread");
      return;
    }
    const before = responseNodes().length;
    ctl.click();
    // SPA route change -- composer should clear, prior responses drop away.
    await waitFor(
      () => {
        const c = findComposer();
        return c && (c.textContent || "").trim() === "" && responseNodes().length < before + 1;
      },
      { timeout: 6000 }
    );
    await sleep(300);
  }

  // The dedicated "Videos" composer is the only place with aspect-ratio controls.
  // Enter it via the sidebar link -- an SPA route change, NOT a full navigation,
  // so the content script and its WebSocket survive (a reload would drop the job).
  function videosReady() {
    return !!document.querySelector('button[aria-label^="Aspect ratio"]') && !!findComposer();
  }
  async function enterVideosMode() {
    if (videosReady()) return true;
    const link =
      document.querySelector('a[href="/videos"]') ||
      document.querySelector('a[aria-label="Videos" i]');
    if (link) link.click();
    return !!(await waitFor(videosReady, { timeout: 15000 }));
  }

  // Pick 16:9 (landscape, default) or 9:16 (portrait) with Gemini's real buttons.
  async function setAspectRatio(aspect) {
    const trig = document.querySelector('button[aria-label^="Aspect ratio"]');
    if (!trig) return; // control absent -> leave the UI default
    const wantPortrait = /9\s*:\s*16|portrait|vertical/i.test(aspect || "");
    const label = wantPortrait ? "Portrait (9:16)" : "Landscape (16:9)";
    if ((trig.getAttribute("aria-label") || "").includes(label)) return; // already set
    trig.click();
    const opt = await waitFor(
      () =>
        [...document.querySelectorAll('[role="menuitemradio"]')].find((m) =>
          (m.getAttribute("aria-label") || "").includes(label)
        ),
      { timeout: 4000 }
    );
    if (opt) {
      opt.click();
      await sleep(300);
    }
  }

  function setPrompt(el, text) {
    el.focus();
    // clear existing content, then insert -- execCommand triggers the editor's
    // own input handling (Gemini uses a Quill-like rich editor).
    const sel = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(el);
    sel.removeAllRanges();
    sel.addRange(range);
    document.execCommand("delete", false);
    // insertText keeps newlines; do it in one shot.
    const ok = document.execCommand("insertText", false, text);
    if (!ok) {
      // fallback: set innerText + fire input
      el.innerText = text;
      el.dispatchEvent(new InputEvent("input", { bubbles: true, data: text }));
    }
    el.dispatchEvent(new Event("input", { bubbles: true }));
  }

  async function scrapeWhenComplete(baselineCount, onDelta) {
    // 1) wait for a new response node to appear
    const appeared = await waitFor(() => responseNodes().length > baselineCount, {
      timeout: 30000,
    });
    if (!appeared) throw new Error("no response appeared");

    // 2) poll the last node until the text is stable. The stop button is only a
    // hint: since the 2026-07 UI update a visible "Stop response" button can
    // PERSIST after generation ends, so requiring it to vanish stalls every
    // chat to the hard timeout. Text stability alone finishes the job:
    // ~1.6s stable + no stop button, or ~4s stable regardless of the button.
    let last = "";
    let stable = 0;
    const t0 = Date.now();
    const HARD = 290000;
    for (;;) {
      dismissOnboarding(); // the "Keep in mind" notice can pop mid-response
      const nodes = responseNodes();
      const node = nodes[nodes.length - 1];
      const text = node ? (node.innerText || "").trim() : "";
      if (onDelta && text && text !== last) onDelta(text);
      const generating = !!findStopButton();
      if (text && text === last) {
        stable += 1;
      } else {
        stable = 0;
      }
      last = text;
      if (text && stable >= 4 && !generating) return text;
      if (text && stable >= 10) return text;
      if (Date.now() - t0 > HARD) return text || "";
      await sleep(400);
    }
  }

  async function handleChat(job) {
    const { id, prompt, fresh } = job;
    LOG("job", id, "fresh=", fresh, "prompt:", prompt.slice(0, 80));
    try {
      dismissOnboarding();
      if (fresh) await startNewChat();
      const composer = await waitFor(findComposer, { timeout: 15000 });
      if (!composer) throw new Error("composer not found");
      const baseline = responseNodes().length;
      setPrompt(composer, prompt);
      const sendBtn = await waitFor(findSendButton, { timeout: 8000 });
      if (!sendBtn) throw new Error("send button not found");
      await sleep(150);
      sendBtn.click();

      let lastSent = "";
      const text = await scrapeWhenComplete(baseline, (partial) => {
        if (partial.length > lastSent.length) {
          lastSent = partial;
          sendMsg({ type: "delta", id, text: partial });
        }
      });
      sendMsg({ type: "result", id, text });
      LOG("done", id, `${text.length} chars`);
    } catch (e) {
      LOG("error", id, e.message);
      sendMsg({ type: "error", id, message: String(e.message || e) });
    }
  }

  // ---- media generation (image / video) ----
  // Gemini renders a generated image as <single-image><img src="blob:..."> and a
  // generated video as <video src="blob:...">. Both are same-origin blobs.

  // Only a *generated* image counts. Gemini also renders web-search results
  // inside <single-image> (an Unsplash/gstatic stock photo when it decides to
  // search instead of generate) -- those are cross-origin search thumbnails and
  // must never be returned as if generated. A real generated image is a
  // same-origin blob:/data: or a googleusercontent URL (Imagen output).
  const SEARCH_THUMB = /gstatic\.com|encrypted-tbn|images\.unsplash\.com|\.unsplash\.com/i;
  function isGeneratedImg(i) {
    const s = i.currentSrc || i.src || "";
    if (!s || SEARCH_THUMB.test(s)) return false;
    return s.startsWith("blob:") || s.startsWith("data:") || /googleusercontent\.com/i.test(s);
  }
  function findGeneratedImages() {
    const ok = (i) =>
      visible(i) && i.naturalWidth >= 200 && i.naturalHeight >= 200 && isGeneratedImg(i);
    let imgs = [...document.querySelectorAll("single-image img")].filter(ok);
    if (!imgs.length) imgs = [...document.querySelectorAll("img")].filter(ok);
    return imgs;
  }

  function findGeneratedVideos() {
    return [...document.querySelectorAll("video")].filter(
      (v) => visible(v) && (v.currentSrc || v.src)
    );
  }

  // Image -> bytes. Gemini renders the generated image either as a same-origin
  // blob: (canvas export is instant and taint-free) OR as a cross-origin https
  // URL (lh3.googleusercontent.com), which taints the canvas so toDataURL()
  // throws "Tainted canvases may not be exported". For the https case we fetch
  // the bytes directly from the isolated world (host_permissions + credentials),
  // exactly like the video path. Canvas is tried first for blobs, with a fetch
  // fallback if export is ever blocked.
  async function grabImage(img) {
    const src = img.currentSrc || img.src || "";
    if (src.startsWith("blob:") || src.startsWith("data:")) {
      try {
        const cv = document.createElement("canvas");
        cv.width = img.naturalWidth;
        cv.height = img.naturalHeight;
        cv.getContext("2d").drawImage(img, 0, 0);
        const dataUrl = cv.toDataURL("image/png");
        return { kind: "image", mime: "image/png", b64: dataUrl.split(",")[1] };
      } catch (e) {
        if (!src) throw e; // no URL to fall back to
        // canvas tainted despite blob src -- fall through to authenticated fetch
      }
    }
    return await grabBinary(src, "image");
  }

  // Binary (video) -> base64 by fetching the element's src. Works for same-origin
  // blob: URLs directly; for usercontent URLs the isolated world + host_permissions
  // let it fetch with the browser's own credentials.
  async function grabBinary(url, kind) {
    const resp = await fetch(url, { credentials: "include" });
    if (!resp.ok) throw new Error(`fetch ${kind} failed: HTTP ${resp.status}`);
    const blob = await resp.blob();
    const b64 = await new Promise((res, rej) => {
      const fr = new FileReader();
      fr.onload = () => res(String(fr.result).split(",")[1]);
      fr.onerror = () => rej(new Error("read failed"));
      fr.readAsDataURL(blob);
    });
    return { kind, mime: blob.type || (kind === "video" ? "video/mp4" : "application/octet-stream"), b64 };
  }

  async function handleMedia(job) {
    const { id, prompt, fresh } = job;
    const wantVideo = job.type === "video";
    LOG("media job", id, job.type, "prompt:", prompt.slice(0, 60));
    try {
      if (wantVideo) {
        // Video lives in Gemini's dedicated Videos composer, which is also where
        // the aspect-ratio buttons are. Enter it and click the requested ratio.
        if (!(await enterVideosMode())) throw new Error("could not open the Videos composer");
        await setAspectRatio(job.aspect);
      } else if (fresh) {
        await startNewChat();
      }
      dismissOnboarding();
      const composer = await waitFor(findComposer, { timeout: 15000 });
      if (!composer) throw new Error("composer not found");

      // Baseline: any media already on the page (a prior turn's image, an
      // uploaded reference, UI art). We only accept an element whose src is NOT
      // in this set, so we never return stale/unrelated media -- mirrors the
      // baseline gating handleChat uses for text.
      const finder = wantVideo ? findGeneratedVideos : findGeneratedImages;
      const srcOf = (el) => el.currentSrc || el.src || "";
      const before = new Set(finder().map(srcOf).filter(Boolean));

      // Force the *generation* tool, not web search. For images, Gemini Flash will
      // happily reply with an Unsplash stock photo + "prompt tips" if the instruction
      // is ambiguous, so the framing must be an explicit create-it command that
      // forbids searching -- even when the prompt already says "photo/image". Video
      // runs in the Videos composer, which already forces generation, so the prompt
      // is the plain description.
      const framed = wantVideo
        ? prompt
        : `Generate an image of the following. Create the image yourself with your image generation tool; do not search the web and do not return a stock photo: ${prompt}`;
      setPrompt(composer, framed);
      const sendBtn = await waitFor(findSendButton, { timeout: 8000 });
      if (!sendBtn) throw new Error("send button not found");
      await sleep(150);
      sendBtn.click();

      // Wait for a NEW media element that is actually READY. Note: Gemini keeps
      // the stop button up while it writes trailing text AFTER the media is done,
      // so we key off media readiness, not the stop button.
      //   image -> findGeneratedImages already requires naturalWidth>=200, i.e. the
      //            blob has decoded, so a fresh candidate is ready to grab.
      //   video -> ready once it can play (videoWidth>0) or generation has ended.
      const timeout = wantVideo ? 600000 : 420000;
      const el = await waitFor(
        () => {
          // The first-use "Keep in mind / Got it" notice can pop AFTER the prompt
          // is sent, covering the response; dismissing only at job start misses
          // it, so keep trying on every tick.
          dismissOnboarding();
          const fresh2 = finder().filter((e) => {
            const s = srcOf(e);
            return s && !before.has(s);
          });
          if (!fresh2.length) {
            const q = quotaLimitText();
            if (q) return { quota: q };
            return null;
          }
          const cand = fresh2[fresh2.length - 1];
          if (wantVideo) {
            return cand.videoWidth > 0 || !findStopButton() ? cand : null;
          }
          return cand; // image already decoded (>=200px) via the finder filter
        },
        { timeout, interval: wantVideo ? 2000 : 800 }
      );
      if (el && el.quota) throw new Error(`media quota exhausted on this account: ${el.quota}`);
      if (!el) throw new Error(`${job.type} did not render in time (quota or slow generation)`);
      await sleep(600); // let the final frame/src settle

      let media;
      if (wantVideo) {
        const src = el.currentSrc || el.src;
        media = await grabBinary(src, "video");
      } else {
        media = await grabImage(el);
      }
      const text = (responseNodes().slice(-1)[0]?.innerText || "").trim();
      sendMsg({ type: "result", id, media: [media], text });
      LOG("media done", id, media.kind, `${media.b64.length} b64 chars`);
    } catch (e) {
      LOG("media error", id, e.message);
      sendMsg({ type: "error", id, message: String(e.message || e) });
    }
  }

  // ---- websocket lifecycle ----
  let ws = null;
  let backoff = 1000;
  let busy = false;
  let lastJobActivity = Date.now();
  // Worker identity survives page reloads (sessionStorage is per-tab): once a
  // tab has served a bridge job it stays a worker even when it reconnects from
  // inside a bridge-created conversation after a reload/server restart.
  let servedJob = false;
  try { servedJob = sessionStorage.getItem("gcb_worker") === "1"; } catch (e) {}

  // Idle worker tabs go STALE: after ~10min the page stops rendering generated
  // media (jobs time out). Self-refresh keeps this tab young. Never reloads the
  // human's tabs: only bare /app tabs or conversations this bridge created, and
  // never while focused, busy, or with text sitting in the composer.
  const STALE_MS = 5 * 60 * 1000 + Math.floor(Math.random() * 60 * 1000);
  setInterval(() => {
    if (busy || shuttingDown) return;
    if (Date.now() - lastJobActivity < STALE_MS) return;
    if (document.hasFocus()) return;
    const c = findComposer();
    if (c && (c.textContent || "").trim()) return;
    const inConversation = /\/app\/[0-9a-f]{6,}/.test(location.pathname);
    if (inConversation && !servedJob) return; // the human's thread -- hands off
    LOG("stale-refresh: reloading tab to stay generation-capable");
    location.reload();
  }, 60 * 1000);

  // Always reply over the CURRENT socket: a job can outlive the socket it
  // arrived on (reconnects during long media jobs), and sending on the stale
  // one silently drops the reply -- the server then times out with no message.
  function sendMsg(obj) {
    try {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(obj));
        return true;
      }
    } catch (e) {}
    LOG("reply dropped, socket not open:", obj.type, obj.id || "");
    return false;
  }

  // Stable per-tab identity so the server can pool tabs and route jobs.
  // sessionStorage survives page reloads but not tab close -- exactly a "tab".
  const TAB_ID = (() => {
    try {
      let id = sessionStorage.getItem("gcb_tab_id");
      if (!id) {
        id = "tab-" + Math.random().toString(36).slice(2, 8);
        sessionStorage.setItem("gcb_tab_id", id);
      }
      return id;
    } catch {
      return "tab-" + Math.random().toString(36).slice(2, 8);
    }
  })();
  // Google multi-login account this tab is on (/u/N/ in the URL; default 0).
  const authuserOf = () => (location.pathname.match(/\/u\/(\d+)\//) || [])[1] || "0";

  function connect() {
    const wsUrl = `ws://localhost:${PORT}/ws`;
    LOG("connecting", wsUrl);
    try {
      ws = new WebSocket(wsUrl);
    } catch (e) {
      scheduleReconnect();
      return;
    }
    ws.onopen = () => {
      LOG("connected");
      backoff = 1000;
      ws.send(JSON.stringify({
        type: "hello", tabId: TAB_ID, authuser: authuserOf(), href: location.href,
        worker: servedJob,
      }));
    };
    ws.onmessage = async (ev) => {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (msg.type === "ping") {
        ws.send(JSON.stringify({ type: "pong" }));
        return;
      }
      if (msg.type === "reload") {
        LOG("server requested extension reload");
        try { chrome.runtime.sendMessage({ cmd: "gcb-reload" }); } catch (e) {}
        return;
      }
      if (msg.type === "chat" || msg.type === "image" || msg.type === "video") {
        if (busy) {
          ws.send(
            JSON.stringify({ type: "error", id: msg.id, message: "busy with another request" })
          );
          return;
        }
        busy = true;
        servedJob = true;
        try { sessionStorage.setItem("gcb_worker", "1"); } catch (e) {}
        lastJobActivity = Date.now();
        try {
          if (msg.type === "chat") await handleChat(msg);
          else await handleMedia(msg);
        } finally {
          busy = false;
          lastJobActivity = Date.now();
        }
      }
    };
    ws.onclose = () => {
      LOG("closed");
      scheduleReconnect();
    };
    ws.onerror = () => {
      try {
        ws.close();
      } catch {}
    };
  }

  function scheduleReconnect() {
    if (shuttingDown) return;
    setTimeout(connect, backoff);
    backoff = Math.min(backoff * 1.5, 15000);
  }

  function shutdown(why) {
    if (shuttingDown) return;
    shuttingDown = true;
    LOG("shutting down:", why);
    try { if (ws) ws.close(); } catch (e) {}
    ws = null;
  }

  // Watchdog: a newer injected copy took over, or the extension was reloaded
  // away (orphan -> chrome.runtime access throws). Either way, stop competing.
  setInterval(() => {
    if (shuttingDown) return;
    if (document.documentElement.dataset.gcbGen !== MY_GEN) {
      shutdown("superseded by newer content script");
      return;
    }
    try {
      chrome.runtime.getURL("");
    } catch (e) {
      shutdown("orphaned (extension reloaded)");
    }
  }, 4000);

  // Read an optional port override from storage, then connect.
  try {
    chrome.storage.local.get(["port"], (r) => {
      if (r && r.port) PORT = r.port;
      connect();
    });
  } catch (e) {
    connect();
  }
  LOG("content script loaded on", location.href);
})();
