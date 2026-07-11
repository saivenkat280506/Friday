/**
 * popup.js — FRIDAY Eye Chrome Extension Popup Controller
 * ========================================================
 * Handles screen capture, backend communication, and continuous mode.
 */

const BACKEND_URL = "http://localhost:8000";
const HEALTH_URL = `${BACKEND_URL}/health`;
const VISION_URL = `${BACKEND_URL}/agent/vision`;

let continuousInterval = null;

// ── DOM Elements ─────────────────────────────────────────
const statusDot = document.getElementById("statusDot");
const statusLabel = document.getElementById("statusLabel");
const captureBtn = document.getElementById("captureBtn");
const lastCapture = document.getElementById("lastCapture");
const statusMsg = document.getElementById("statusMsg");
const continuousToggle = document.getElementById("continuousToggle");

// ── Health Check ─────────────────────────────────────────
async function checkHealth() {
  try {
    const resp = await fetch(HEALTH_URL, { method: "GET" });
    if (resp.ok) {
      statusDot.classList.add("connected");
      statusLabel.textContent = "Connected";
      captureBtn.disabled = false;
      return true;
    }
  } catch (e) {
    // Backend unreachable
  }
  statusDot.classList.remove("connected");
  statusLabel.textContent = "Disconnected";
  captureBtn.disabled = true;
  return false;
}

// ── Capture Screen ───────────────────────────────────────
async function captureScreen() {
  setStatus("Capturing...", "");

  try {
    // Get the current active tab
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab) {
      setStatus("No active tab found.", "error");
      return;
    }

    // Capture the visible tab as a data URL
    const dataUrl = await chrome.tabs.captureVisibleTab(null, {
      format: "jpeg",
      quality: 75,
    });

    // Extract base64 from data URL (remove "data:image/jpeg;base64," prefix)
    const base64 = dataUrl.split(",")[1];

    setStatus("Analyzing...", "");

    // Send to FRIDAY backend
    const resp = await fetch(VISION_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ image_base64: base64 }),
    });

    if (resp.ok) {
      const data = await resp.json();
      const desc = data.description || "Analysis complete.";
      setStatus(desc.substring(0, 120) + (desc.length > 120 ? "..." : ""), "success");
      lastCapture.textContent = new Date().toLocaleTimeString();
    } else {
      setStatus(`Backend error: ${resp.status}`, "error");
    }
  } catch (e) {
    console.error("[FRIDAY Eye] Capture error:", e);
    setStatus(`Error: ${e.message}`, "error");
  }
}

// ── Continuous Mode ──────────────────────────────────────
function startContinuous() {
  if (continuousInterval) return;
  continuousInterval = setInterval(async () => {
    const healthy = await checkHealth();
    if (healthy) {
      // Send capture message to background script (works even when popup closes)
      chrome.runtime.sendMessage({ action: "capture" });
    }
  }, 5000);
  setStatus("Continuous mode ON", "success");
}

function stopContinuous() {
  if (continuousInterval) {
    clearInterval(continuousInterval);
    continuousInterval = null;
  }
  // Also clear any alarms in the background
  chrome.runtime.sendMessage({ action: "stop_continuous" });
  setStatus("Continuous mode OFF", "");
}

// ── Status Helper ────────────────────────────────────────
function setStatus(msg, type) {
  statusMsg.textContent = msg;
  statusMsg.className = "status-msg" + (type ? ` ${type}` : "");
}

// ── Event Listeners ──────────────────────────────────────
captureBtn.addEventListener("click", captureScreen);

continuousToggle.addEventListener("change", (e) => {
  if (e.target.checked) {
    startContinuous();
    // Also tell background to set up alarms
    chrome.runtime.sendMessage({ action: "start_continuous" });
  } else {
    stopContinuous();
  }
});

// ── Initialize ───────────────────────────────────────────
checkHealth();
// Re-check every 10 seconds
setInterval(checkHealth, 10000);
