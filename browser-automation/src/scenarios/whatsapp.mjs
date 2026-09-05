/**
 * WhatsApp Web fallback for macOS when WhatsApp.app is missing or the
 * desktop composer did not accept text. Requires an already-logged-in
 * Chrome profile (clone or live). First run shows a QR code.
 */
import { getPage } from "../browser.mjs";

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function dismissModals(page) {
  try {
    await page.evaluate(() => {
      const nodes = [...document.querySelectorAll("button, div[role='button']")];
      const hit = nodes.find((el) =>
        /continue|ok|got it|use here|not now|close/i.test((el.innerText || "").trim())
      );
      if (hit) hit.click();
    });
  } catch {
    /* ignore */
  }
}

async function waitForSession(page, timeoutMs = 25000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (page.isClosed()) return { ok: false, error: "page-closed" };
    const state = await page.evaluate(() => {
      const qr = document.querySelector(
        'canvas[aria-label*="scan" i], div[data-testid="qrcode"], canvas[aria-label*="QR" i]'
      );
      const chat = document.querySelector(
        '#pane-side, [data-testid="chat-list"], div[aria-label*="Chat list" i]'
      );
      return { qr: !!qr, chat: !!chat };
    }).catch(() => ({ qr: false, chat: false }));
    if (state.chat) return { ok: true, loggedIn: true };
    if (state.qr) return { ok: false, needsScan: true };
    await sleep(500);
  }
  return { ok: false, error: "whatsapp-web-timeout" };
}

async function openChat(page, phone, name) {
  const query = (phone || name || "").replace(/\s+/g, "");
  const searchSelectors = [
    'div[contenteditable="true"][data-tab="3"]',
    'div[title="Search input textbox"]',
    'div[aria-label*="Search" i][contenteditable="true"]',
  ];
  let box = null;
  for (const sel of searchSelectors) {
    box = await page.$(sel);
    if (box) break;
  }
  if (!box) {
    await page.keyboard.down("Meta");
    await page.keyboard.press("KeyF");
    await page.keyboard.up("Meta");
    await sleep(400);
  }
  const target = box || (await page.$('div[contenteditable="true"]'));
  if (!target) return false;
  await target.click({ clickCount: 3 });
  await page.keyboard.press("Backspace");
  await page.type('div[contenteditable="true"]', query, { delay: 20 }).catch(async () => {
    await page.keyboard.type(query, { delay: 20 });
  });
  await sleep(900);
  await page.keyboard.press("Enter");
  await sleep(800);
  return true;
}

export async function sendWhatsApp(params = {}) {
  const phone = String(params.phone || params.number || "").replace(/\D/g, "");
  const name = String(params.name || params.contact || "").trim();
  const message = String(params.message || params.text || "").trim();
  if (!phone && !name) {
    return { ok: false, error: "Need a saved contact or phone number." };
  }

  const page = await getPage();
  await page.goto("https://web.whatsapp.com", {
    waitUntil: "domcontentloaded",
    timeout: 90000,
  });
  await sleep(1200);
  await dismissModals(page);

  const session = await waitForSession(page);
  if (session.needsScan) {
    return {
      ok: false,
      needsScan: true,
      message: "WhatsApp Web is open. Scan the QR code once in this Chrome profile, Boss.",
    };
  }
  if (!session.ok) {
    return { ok: false, error: session.error || "WhatsApp Web did not load." };
  }

  const opened = await openChat(page, phone, name);
  if (!opened) {
    return { ok: false, error: "Could not open the WhatsApp Web search box." };
  }
  if (!message) {
    return { ok: true, message: `Opened WhatsApp Web chat for ${name || phone}.` };
  }

  const composer = await page.$(
    'footer div[contenteditable="true"], div[contenteditable="true"][data-tab="10"]'
  );
  if (!composer) {
    return { ok: false, error: "WhatsApp Web composer was not found." };
  }
  await composer.click();
  await sleep(200);
  await page.keyboard.type(message, { delay: 15 });
  await sleep(250);
  await page.keyboard.press("Enter");
  await sleep(500);
  return { ok: true, message: "WhatsApp message sent via WhatsApp Web, Boss." };
}
