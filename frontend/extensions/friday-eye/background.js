/**
 * background.js — FRIDAY Eye Service Worker
 * ==========================================
 * Handles screen capture requests from popup and continuous mode via alarms.
 */

const BACKEND_URL = "http://localhost:8000";
const VISION_URL = `${BACKEND_URL}/agent/vision`;
const OLLAMA_URL = "http://localhost:11434/api/generate";

// ── Message Handler ──────────────────────────────────────
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.action === "capture") {
    captureAndSend().then(sendResponse).catch((e) => {
      console.error("[FRIDAY Eye BG] Capture error:", e);
      sendResponse({ error: e.message });
    });
    return true; // Keep channel open for async response
  }

  if (message.action === "start_continuous") {
    chrome.alarms.create("friday_capture", { periodInMinutes: 5 / 60 }); // ~every 5 seconds
    console.log("[FRIDAY Eye BG] Continuous capture alarm started.");
    sendResponse({ status: "started" });
  }

  if (message.action === "stop_continuous") {
    chrome.alarms.clear("friday_capture");
    console.log("[FRIDAY Eye BG] Continuous capture alarm stopped.");
    sendResponse({ status: "stopped" });
  }
});

// ── Alarm Handler (Continuous Mode) ──────────────────────
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "friday_capture") {
    captureAndSend().catch((e) => {
      console.error("[FRIDAY Eye BG] Alarm capture error:", e);
    });
  }
});

// ── Core Capture Logic ───────────────────────────────────
async function captureAndSend() {
  try {
    // Capture the active tab
    const dataUrl = await chrome.tabs.captureVisibleTab(null, {
      format: "jpeg",
      quality: 75,
    });

    const base64 = dataUrl.split(",")[1];

    // Try FRIDAY backend first
    try {
      const resp = await fetch(VISION_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ image_base64: base64 }),
      });

      if (resp.ok) {
        const data = await resp.json();
        console.log("[FRIDAY Eye BG] Backend analysis:", data.description?.substring(0, 100));
        return { status: "ok", provider: "friday", description: data.description };
      }
    } catch (e) {
      console.warn("[FRIDAY Eye BG] Backend unavailable, trying Ollama...");
    }

    // Fallback: Try Ollama directly
    try {
      const resp = await fetch(OLLAMA_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model: "moondream:latest",
          prompt: "Describe all visible UI elements, text, buttons, and their positions on this computer screen. Be concise.",
          images: [base64],
          stream: false,
        }),
      });

      if (resp.ok) {
        const data = await resp.json();
        console.log("[FRIDAY Eye BG] Ollama analysis:", data.response?.substring(0, 100));
        return { status: "ok", provider: "ollama", description: data.response };
      }
    } catch (e) {
      console.warn("[FRIDAY Eye BG] Ollama also unavailable.");
    }

    return { status: "error", message: "No vision provider available" };
  } catch (e) {
    console.error("[FRIDAY Eye BG] Capture failed:", e);
    return { status: "error", message: e.message };
  }
}
