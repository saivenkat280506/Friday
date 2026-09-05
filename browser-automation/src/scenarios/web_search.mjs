/**
 * Open a search page, extract results, do not run the old scroll demo.
 */
import * as actions from "../actions.mjs";
import { setWindowBounds } from "../browser.mjs";

const EXTRACT_RESULTS = () => {
  const clean = (s) => (s || "").replace(/\s+/g, " ").trim();
  const results = [];
  const seen = new Set();

  const push = (title, url, snippet) => {
    title = clean(title);
    url = clean(url);
    snippet = clean(snippet);
    if (!title || !url) return;
    if (url.startsWith("//")) url = `https:${url}`;
    try {
      const parsed = new URL(url, location.href);
      const uddg = parsed.searchParams.get("uddg");
      if (uddg) url = decodeURIComponent(uddg);
      else url = parsed.href;
    } catch {
      /* keep */
    }
    if (!/^https?:\/\//i.test(url)) return;
    if (/google\.[^/]+\/(aclk|search|sorry|url\?)/i.test(url)) return;
    if (/support\.google|accounts\.google|policies\.google|consent\.google|myaccount\.google/i.test(url)) return;
    if (/duckduckgo\.com\/(y\.js|l\/\?)/i.test(url)) return;
    const key = url.split("#")[0];
    if (seen.has(key)) return;
    seen.add(key);
    results.push({ title: title.slice(0, 180), url, snippet: snippet.slice(0, 320) });
  };

  const featured =
    clean(document.querySelector("[data-attrid='wa:/description']")?.innerText) ||
    clean(document.querySelector(".kno-rdesc")?.innerText) ||
    clean(document.querySelector(".IZ6rdc")?.innerText) ||
    clean(document.querySelector("#knowledge-graph .hgKElc")?.innerText) ||
    "";

  const googleBlocks = document.querySelectorAll("#search .MjjYud, #rso .g, #search div.g, #links .result");
  googleBlocks.forEach((block) => {
    const a = block.querySelector("a[href^='http']");
    const h3 = block.querySelector("h3") || block.querySelector(".result__a") || a;
    const snip =
      block.querySelector("[data-sncf], .VwiC3b, .IsZvec, .result__snippet")?.innerText ||
      "";
    if (a && h3) push(h3.innerText || a.innerText, a.href, snip);
  });

  if (!results.length) {
    document.querySelectorAll("a[href^='http']").forEach((a) => {
      const title = a.innerText;
      if (title && title.length > 12) push(title, a.href, a.closest("article, li, div")?.innerText || "");
    });
  }

  return { featured, results: results.slice(0, 6) };
};

function usableResults(extracted) {
  const rows = extracted?.results || [];
  return rows.filter((r) => r?.url && !/google\./i.test(r.url) && !/duckduckgo\.com\/\?q=/i.test(r.url));
}

export async function searchAndBrief({ query, bounds = null } = {}) {
  const q = (query || "").trim();
  if (!q) throw new Error("Missing search query");

  if (bounds && (bounds.width || bounds.left != null || bounds.x != null)) {
    await setWindowBounds(bounds);
  }

  const pageMod = await import("../browser.mjs");
  const googleUrl = `https://www.google.com/search?hl=en&gl=us&pws=0&q=${encodeURIComponent(q)}`;
  await actions.navigate(googleUrl, { waitUntil: "domcontentloaded", timeout: 45000 });
  await actions.wait(800);
  await actions.dismissBanners();
  await actions.wait(900);

  const page = await pageMod.getPage();
  let extracted = { featured: "", results: [] };
  try {
    extracted = await page.evaluate(EXTRACT_RESULTS);
  } catch {
    extracted = { featured: "", results: [] };
  }

  if (usableResults(extracted).length < 2) {
    const ddg = `https://html.duckduckgo.com/html/?q=${encodeURIComponent(q)}`;
    await actions.navigate(ddg, { waitUntil: "domcontentloaded", timeout: 30000 });
    await actions.wait(900);
    try {
      extracted = await page.evaluate(EXTRACT_RESULTS);
    } catch {
      extracted = { featured: "", results: [] };
    }
  }

  if (bounds && (bounds.width || bounds.left != null)) {
    await setWindowBounds(bounds);
  }

  const info = await actions.contentInfo();
  return {
    ok: true,
    query: q,
    featured: extracted.featured || "",
    results: extracted.results || [],
    ...info,
    message: `Opened search results for "${q}".`,
  };
}

/** Back-compat name used by the control server. */
export async function searchAndScroll(params = {}) {
  return searchAndBrief(params);
}
