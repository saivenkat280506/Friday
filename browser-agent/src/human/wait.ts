export function randomBetween(min: number, max: number): number {
  return min + Math.random() * (max - min);
}

export async function humanWait(minMs = 300, maxMs = 1200): Promise<void> {
  const delay = randomBetween(minMs, maxMs);
  await new Promise((resolve) => setTimeout(resolve, delay));
}

/** Alias matching the FRIDAY automation spec (`randomDelay`). */
export const randomDelay = humanWait;

export async function shortPause(minMs = 80, maxMs = 220): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, randomBetween(minMs, maxMs)));
}