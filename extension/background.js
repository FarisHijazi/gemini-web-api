/* MV3 service worker.
 *
 * The content script does the real work (owns the WebSocket, drives the page).
 * The worker's jobs:
 *   1. AUTO-INJECT content.js into every open gemini.google.com tab whenever the
 *      extension (re)loads -- so a code update or chrome.runtime.reload() never
 *      needs a human to reload tabs by hand. Freshly injected scripts take over;
 *      orphaned old ones shut themselves down (see content.js generation guard).
 *   2. Relay the "gcb-reload" command from a content script (sent when the
 *      server broadcasts {"type":"reload"}) into chrome.runtime.reload().
 *   3. Toolbar action: open a Gemini tab if none exists.
 */

async function injectAll(reason) {
  let tabs = [];
  try {
    tabs = await chrome.tabs.query({ url: "https://gemini.google.com/*" });
  } catch (e) {
    console.log("[GCB] tabs.query failed:", e.message);
    return;
  }
  console.log(`[GCB] (${reason}) injecting content.js into ${tabs.length} gemini tab(s)`);
  for (const t of tabs) {
    try {
      await chrome.scripting.executeScript({ target: { tabId: t.id }, files: ["content.js"] });
    } catch (e) {
      console.log("[GCB] inject failed for tab", t.id, e.message);
    }
  }
}

chrome.runtime.onInstalled.addListener(() => injectAll("installed/reloaded"));
chrome.runtime.onStartup.addListener(() => injectAll("browser-startup"));

chrome.runtime.onMessage.addListener((msg) => {
  if (msg && msg.cmd === "gcb-reload") {
    console.log("[GCB] reload requested -- reloading extension");
    chrome.runtime.reload();
  }
});

// Backup reload path: poll the server for a pending reload request (the primary
// path -- a WS broadcast relayed by a content script -- can miss if no content
// script is alive to relay). chrome.alarms survives MV3 worker suspension.
chrome.alarms.create("gcb-reload-poll", { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name !== "gcb-reload-poll") return;
  try {
    const r = await fetch("http://localhost:8100/v1/extension/pending-reload");
    if (r.ok && (await r.json()).reload) {
      console.log("[GCB] server requested reload (poll)");
      chrome.runtime.reload();
    }
  } catch (e) { /* server down -- fine */ }
});

chrome.action.onClicked?.addListener?.(async () => {
  const tabs = await chrome.tabs.query({ url: "https://gemini.google.com/*" });
  if (tabs.length === 0) {
    chrome.tabs.create({ url: "https://gemini.google.com/app" });
  }
});
