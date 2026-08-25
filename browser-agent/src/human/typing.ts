import type { ElementHandle, Page } from "puppeteer";
import { randomBetween, shortPause } from "./wait.js";

const ADJACENT_KEYS: Record<string, string[]> = {
  a: ["s", "q", "z"],
  b: ["v", "n", "g"],
  c: ["x", "v", "d"],
  d: ["s", "f", "e", "c"],
  e: ["w", "r", "d"],
  f: ["d", "g", "r"],
  g: ["f", "h", "t", "b"],
  h: ["g", "j", "y", "n"],
  i: ["u", "o", "k"],
  j: ["h", "k", "u", "m"],
  k: ["j", "l", "i", "m"],
  l: ["k", "o", "p"],
  m: ["n", "j", "k"],
  n: ["b", "m", "h"],
  o: ["i", "p", "l"],
  p: ["o", "l"],
  q: ["w", "a"],
  r: ["e", "t", "f"],
  s: ["a", "d", "w", "x"],
  t: ["r", "y", "g"],
  u: ["y", "i", "j"],
  v: ["c", "b", "f"],
  w: ["q", "e", "s"],
  x: ["z", "c", "s"],
  y: ["t", "u", "h"],
  z: ["a", "x"],
};

function typoChar(char: string): string {
  const lower = char.toLowerCase();
  const neighbors = ADJACENT_KEYS[lower];
  if (!neighbors?.length) {
    return String.fromCharCode(char.charCodeAt(0) + (Math.random() < 0.5 ? -1 : 1));
  }
  const pick = neighbors[Math.floor(Math.random() * neighbors.length)];
  return char === char.toUpperCase() ? pick.toUpperCase() : pick;
}

export async function humanType(
  page: Page,
  element: ElementHandle<Element>,
  text: string,
): Promise<void> {
  await element.click({ clickCount: 3 });
  await shortPause(100, 250);
  await page.keyboard.press("Backspace");
  await shortPause(80, 160);

  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    if (Math.random() < 0.02 && i > 0 && char.trim()) {
      const wrong = typoChar(char);
      await page.keyboard.type(wrong, { delay: randomBetween(40, 90) });
      await shortPause(120, 280);
      await page.keyboard.press("Backspace");
      await shortPause(80, 150);
    }
    await page.keyboard.type(char, { delay: randomBetween(40, 180) });
    if (char === " ") {
      await shortPause(120, 320);
    }
  }
}