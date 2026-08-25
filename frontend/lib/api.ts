// Browser uses Next.js rewrite (/backend → :8000) to avoid cross-origin health failures.
export const BACKEND_URL =
  typeof window !== "undefined" ? "/backend" : "http://127.0.0.1:8000";
export const WS_URL = "ws://127.0.0.1:8000/ws";