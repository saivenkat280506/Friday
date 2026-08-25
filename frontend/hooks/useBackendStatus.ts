"use client";

import { useState, useEffect, useRef } from "react";
import { BACKEND_URL } from "@/lib/api";

export type BackendStatus = "online" | "offline" | "checking" | "starting";

interface BackendHealth {
  status: BackendStatus;
  latency: number | null;
  ready: boolean;
  sttReady: boolean;
}

function fetchWithTimeout(url: string, timeoutMs: number): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  return fetch(url, {
    method: "GET",
    cache: "no-store",
    signal: controller.signal,
  }).finally(() => clearTimeout(timer));
}

function resolveStatus(data: {
  status?: string;
  backend_status?: string;
  ready?: boolean;
}): BackendStatus {
  // If /health returned JSON, the HTTP server is up — do not gate on `ready`
  // (STT/TTS warm in background; `stt_ready` tracks voice separately).
  if (data.backend_status === "online" || data.status === "online") {
    return "online";
  }
  if (data.backend_status === "starting") return "starting";
  return "offline";
}

export function useBackendStatus(intervalMs = 5000): BackendHealth {
  const [status, setStatus] = useState<BackendStatus>("checking");
  const [latency, setLatency] = useState<number | null>(null);
  const [ready, setReady] = useState(false);
  const [sttReady, setSttReady] = useState(false);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const inFlightRef = useRef(false);
  const hasOnlineRef = useRef(false);

  const check = async () => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    if (!hasOnlineRef.current) setStatus("checking");

    const start = performance.now();
    try {
      const res = await fetchWithTimeout(`${BACKEND_URL}/health`, 15000);
      const ms = Math.round(performance.now() - start);
      if (!res.ok) {
        hasOnlineRef.current = false;
        setStatus("offline");
        setLatency(null);
        setReady(false);
        setSttReady(false);
        return;
      }
      const data = (await res.json().catch(() => ({}))) as {
        status?: string;
        backend_status?: string;
        ready?: boolean;
        stt_ready?: boolean;
      };
      const isReady = data.ready !== false;
      setReady(isReady);
      setSttReady(data.stt_ready !== false);
      setLatency(ms);
      const next = resolveStatus(data);
      if (next === "online") hasOnlineRef.current = true;
      setStatus(next);
    } catch {
      hasOnlineRef.current = false;
      setStatus("offline");
      setLatency(null);
      setReady(false);
      setSttReady(false);
    } finally {
      inFlightRef.current = false;
    }
  };

  useEffect(() => {
    check();
    timerRef.current = setInterval(check, intervalMs);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [intervalMs]);

  return { status, latency, ready, sttReady };
}