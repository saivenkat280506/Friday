"use client";

import { useState, useEffect, useRef } from "react";
import { BACKEND_URL } from "@/lib/api";

export type BackendStatus = "online" | "offline" | "checking";

interface BackendHealth {
  status: BackendStatus;
  latency: number | null;
}

export function useBackendStatus(intervalMs = 5000): BackendHealth {
  const [status, setStatus] = useState<BackendStatus>("checking");
  const [latency, setLatency] = useState<number | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const check = async () => {
    const start = performance.now();
    try {
      const res = await fetch(`${BACKEND_URL}/health`, {
        method: "GET",
        cache: "no-store",
        signal: AbortSignal.timeout(10000),
      });
      const ms = Math.round(performance.now() - start);
      if (res.ok || res.status < 500) {
        setStatus("online");
        setLatency(ms);
      } else {
        setStatus("offline");
        setLatency(null);
      }
    } catch {
      setStatus("offline");
      setLatency(null);
    }
  };

  useEffect(() => {
    check();
    timerRef.current = setInterval(check, intervalMs);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [intervalMs]);

  return { status, latency };
}
