"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import { ChatMessage } from "@/components/ChatArea";
import { BACKEND_URL, WS_URL } from "@/lib/api";

export interface ActionLogEntry {
  id: string;
  source: string;
  action: string;
  status: "success" | "pending" | "info" | "error";
  timestamp: string;
  icon?: string;
}

function makeLog(source: string, action: string, status: ActionLogEntry["status"] = "info"): ActionLogEntry {
  return {
    id: Math.random().toString(36).slice(2),
    source,
    action,
    status,
    timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }),
  };
}

export function useFriday() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputText, setInputText] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [streamingText, setStreamingText] = useState("");
  const [speechTranscript, setSpeechTranscript] = useState("");
  const [agentState, setAgentState] = useState<
    "idle" | "idle_listening" | "listening" | "thinking" | "talking" | "transcribing"
  >("idle");
  const [actionLogs, setActionLogs] = useState<ActionLogEntry[]>([]);
  const wsRef = useRef<WebSocket | null>(null);
  const seenMsgsRef = useRef<Map<string, number>>(new Map());
  const reconnectAttemptRef = useRef(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const pushLog = useCallback((entry: ActionLogEntry) => {
    setActionLogs((prev) => {
      const next = [entry, ...prev]; // newest first
      return next.slice(0, 40);      // keep at most 40 entries
    });
  }, []);

  // ── Initialize WebSocket ──────────────────────────────────────────────────
  useEffect(() => {
    const scheduleReconnect = () => {
      if (reconnectTimerRef.current) return;
      const attempt = reconnectAttemptRef.current;
      const delay = Math.min(1000 * Math.pow(2, attempt), 15000);
      reconnectTimerRef.current = setTimeout(() => {
        reconnectTimerRef.current = null;
        reconnectAttemptRef.current = attempt + 1;
        connectWs();
      }, delay);
    };

    const connectWs = () => {
      if (wsRef.current?.readyState === WebSocket.OPEN || wsRef.current?.readyState === WebSocket.CONNECTING) {
        return;
      }
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onopen = () => {
        reconnectAttemptRef.current = 0;
        pushLog(makeLog("WebSocket", "Connected to FRIDAY core", "success"));
      };

      ws.onmessage = (event) => {
        const data = JSON.parse(event.data);

        // ── State updates ──
        if (data.state) {
          setAgentState(data.state as any);
          const stateLabels: Record<string, string> = {
            listening: "Listening for command…",
            thinking: "Processing request…",
            talking: "Generating response…",
            transcribing: "Transcribing audio…",
            idle_listening: "Voice session standby",
            idle: "Standby",
          };
          if (data.state !== "idle") {
            pushLog(makeLog("Core Engine", stateLabels[data.state] || data.state, "info"));
          }
        }

        // ── Wake word ──
        if (data.type === "wake_word_detected") {
          pushLog(makeLog("STT", "Wake word detected", "success"));
        }

        // ── System ready ──
        if (data.type === "system_ready") {
          pushLog(makeLog("System", "All systems online", "success"));
        }

        // ── Response chunk for streaming ──
        if (data.type === "response_chunk") {
          setStreamingText((prev) => prev + data.text);
          setMessages((prev) => {
            const hasStream = prev.some((m) => m.id === "streaming-msg");
            if (!hasStream) {
              const newMsg: ChatMessage = {
                id: "streaming-msg",
                role: "assistant",
                type: "text",
                content: "",
                isStreaming: true,
                time: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false }),
              };
              return [...prev, newMsg];
            }
            return prev;
          });
        }

        // ── TTS actually started — now show speaking state ──
        if (data.type === "tts_started") {
          setAgentState("talking");
        }

        // ── Chat messages from backend (with dedup) ──
        if (data.type === "chat") {
          setStreamingText("");
          // Dedup: skip messages with identical content within 2 seconds
          const dedupKey = `${data.role || "assistant"}:${data.text}`;
          const now = Date.now();
          const lastSeen = seenMsgsRef.current.get(dedupKey);
          if (lastSeen && now - lastSeen < 2000) {
            // Duplicate within 2s window — skip
            return;
          }
          seenMsgsRef.current.set(dedupKey, now);
          // Prune old entries every 20 messages to prevent memory leak
          if (seenMsgsRef.current.size > 50) {
            const cutoff = now - 5000;
            Array.from(seenMsgsRef.current.entries()).forEach(([k, v]) => {
              if (v < cutoff) seenMsgsRef.current.delete(k);
            });
          }

          const newMsg: ChatMessage = {
            id: Math.random().toString(36).substring(7),
            role: data.role || "assistant",
            type: "voice",
            content: data.text,
            time: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false }),
          };
          setMessages((prev) => {
            const filtered = prev.filter((m) => m.id !== "streaming-msg");
            return [...filtered, newMsg];
          });
          pushLog(makeLog("LLM", `Response: "${data.text.slice(0, 50)}${data.text.length > 50 ? "…" : ""}"`, "success"));
        }

        // ── Partial transcript / live STT ──
        if (data.type === "transcript" || data.type === "transcript_chunk" || data.type === "partial_transcript") {
          if (data.countdown !== undefined && data.countdown > 0) {
            setSpeechTranscript(data.text + ` … (sending in ${data.countdown}s)`);
          } else {
            setSpeechTranscript(data.text);
          }
        }

        // ── Final transcript → user message ──
        if (data.type === "user_message") {
          const userMsg: ChatMessage = {
            id: Math.random().toString(36).substring(7),
            role: "user",
            type: "voice",
            content: data.text,
            time: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false }),
          };
          setMessages((prev) => [...prev, userMsg]);
          setSpeechTranscript("");
          pushLog(makeLog("STT", `Finalised: "${data.text}"`, "success"));
        }

        // ── Clear transcript if speech ended empty ──
        if (data.type === "transcript_clear") {
          setSpeechTranscript("");
        }

        // ── Suggestion / Proactive Alert ──
        if (data.type === "suggestion" || data.type === "alert") {
          pushLog(makeLog("System Monitor", data.text, "error"));
        }

        // ── Agent steps (web/OS agent) ──
        if (data.type === "agent_step") {
          const status = data.status === "done" || data.status === "stopped" ? "success" : "pending";
          pushLog(makeLog("Agent", `Step ${data.step}: ${data.action}`, status));
        }

        // ── Companion overlay (voice still runs through the main app pipeline) ──
        if (data.type === "companion_mode") {
          if (data.active) {
            pushLog(makeLog("Companion", "Voice controls active — chat updates here", "info"));
          }
        }

        // ── Focus window ──
        if (data.action === "focus_window") {
          window.focus();
          pushLog(makeLog("UI", "Window focus restored", "info"));
        }
      };

      ws.onclose = () => {
        const nextDelay = Math.min(1000 * Math.pow(2, reconnectAttemptRef.current), 15000);
        pushLog(makeLog("WebSocket", `Connection lost — retrying in ${Math.round(nextDelay / 1000)}s`, "error"));
        setAgentState("idle");
        scheduleReconnect();
      };

      ws.onerror = () => ws.close();
    };

    fetch(`${BACKEND_URL}/health`)
      .then((r) => { if (r.ok) connectWs(); else scheduleReconnect(); })
      .catch(() => scheduleReconnect());

    return () => {
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      wsRef.current?.close();
    };
  }, [pushLog]);

  const isBackendReachable = useCallback(async () => {
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 10000);
      const res = await fetch(`${BACKEND_URL}/health`, {
        method: "GET",
        cache: "no-store",
        signal: controller.signal,
      }).finally(() => clearTimeout(timer));
      return res.ok;
    } catch {
      return false;
    }
  }, []);

  // ── Send message (prefer WS, fallback to HTTP) ─────────────────────────────
  const sendMessage = useCallback(async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;

    const online = await isBackendReachable();
    if (!online) {
      pushLog(makeLog("Chat", "Backend offline — message not sent", "error"));
      setMessages((prev) => [
        ...prev,
        {
          id: "offline-" + Date.now(),
          role: "assistant",
          type: "text",
          content: "Backend offline — message not sent. Run `npm run stop:desktop` then `npm run dev:desktop` to restart.",
          time: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false }),
        },
      ]);
      setAgentState("idle");
      return;
    }

    setInputText("");
    setAgentState("thinking");
    pushLog(makeLog("Chat", `Sent: "${trimmed.slice(0, 60)}"`, "info"));

    const userMsgId = "user-" + Date.now();
    setMessages((prev) => [
      ...prev,
      {
        id: userMsgId,
        role: "user",
        type: "text",
        content: trimmed,
        time: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false }),
      },
    ]);

    // Prefer WebSocket for lower latency, fallback to HTTP
    const payload = {
      event: "command",
      text: trimmed,
      id: userMsgId,
      voice: true,
    };
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(payload));
    } else {
      try {
        const response = await fetch(`${BACKEND_URL}/chat`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: trimmed, id: userMsgId, voice: true }),
        });
        if (!response.ok) throw new Error("HTTP " + response.status);
      } catch (err) {
        pushLog(makeLog("Chat", `Error: ${String(err)}`, "error"));
        setAgentState("idle");
      }
    }
  }, [pushLog, isBackendReachable]);

  // ── Mic Toggle ────────────────────────────────────────────────────────────
  const toggleMic = useCallback(async () => {
    try {
      const stopVoiceStates = new Set([
        "listening",
        "thinking",
        "talking",
        "transcribing",
      ]);
      if (stopVoiceStates.has(agentState)) {
        await fetch(`${BACKEND_URL}/stop-trigger`, { method: "POST" });
        pushLog(makeLog("Voice", "Stop trigger sent", "info"));
        setAgentState("idle");
        return;
      }
      const response = await fetch(`${BACKEND_URL}/listen-trigger`, { method: "POST" });
      if (response.ok) {
        setAgentState("listening");
        pushLog(makeLog("Voice", "Listen trigger activated", "success"));
      }
    } catch (err) {
      pushLog(makeLog("Voice", `Mic error: ${String(err)}`, "error"));
    }
  }, [agentState, pushLog]);

  const clearChat = useCallback(async () => {
    try {
      pushLog(makeLog("UI", "Initializing full systems refresh...", "info"));
      const response = await fetch(`${BACKEND_URL}/reset`, { method: "POST" });
      if (response.ok) {
        setMessages([]);
        pushLog(makeLog("UI", "Chat fully reset: terminated active processes & cleared memory", "success"));
      } else {
        throw new Error(`Server returned status ${response.status}`);
      }
    } catch (err) {
      pushLog(makeLog("UI", `Failed to fully reset chat: ${String(err)}`, "error"));
      // Still clear messages locally as fallback
      setMessages([]);
    }
  }, [pushLog]);

  return {
    messages,
    inputText,
    setInputText,
    settingsOpen,
    setSettingsOpen,
    isListening: agentState === "listening",
    isSpeaking: agentState === "talking",
    streamingText,
    speechTranscript,
    agentState,
    actionLogs,
    sendMessage,
    toggleMic,
    clearChat,
    lastSentence: messages.filter((m) => m.role === "assistant").slice(-1)[0]?.content || "",
  };
}
