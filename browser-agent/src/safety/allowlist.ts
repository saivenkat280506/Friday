const BLOCKED_PROTOCOLS = ["file:", "javascript:", "data:"];

const DEFAULT_ALLOWED_HOSTS = [
  "google.com",
  "www.google.com",
  "duckduckgo.com",
  "open.spotify.com",
  "youtube.com",
  "www.youtube.com",
  "music.youtube.com",
  "news.google.com",
  "chatgpt.com",
  "www.chatgpt.com",
  "openai.com",
  "www.openai.com",
];

export function isUrlAllowed(url: string, extraHosts: string[] = []): boolean {
  try {
    const parsed = new URL(url);
    if (BLOCKED_PROTOCOLS.some((p) => parsed.protocol.startsWith(p.replace(":", "")))) {
      return false;
    }
    const hosts = [...DEFAULT_ALLOWED_HOSTS, ...extraHosts];
    return hosts.some(
      (host) => parsed.hostname === host || parsed.hostname.endsWith(`.${host}`),
    );
  } catch {
    return false;
  }
}