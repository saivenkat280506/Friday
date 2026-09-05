"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import { ChatMessage } from "@/components/ChatArea";
import { SearchBriefingData } from "@/components/SearchBriefing";
import { AgentState } from "@/components/ui/orb";
import { useBackendStatus } from "@/hooks/useBackendStatus";
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
  const [agentState, setAgentState] = useState<AgentState>("idle");
  const [actionLogs, setActionLogs] = useState<ActionLogEntry[]>([]);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const wasOfflineRef = useRef(false);
  const backendStatusRef = useRef<string>("checking");
  const chatAbortRef = useRef<AbortController | null>(null);

  const { status: backendStatus, latency: backendLatency } = useBackendStatus(5000);

  useEffect(() => {
    backendStatusRef.current = backendStatus;
  }, [backendStatus]);
  const isBackendOnline = backendStatus === "online";

  const pushLog = useCallback((entry: ActionLogEntry) => {
    setActionLogs((prev) => {
      const next = [entry, ...prev];
      return next.slice(0, 40);
    });
  }, []);

  const resetUiToOffline = useCallback(() => {
    setAgentState("offline");
    setStreamingText("");
    setSpeechTranscript("");
  }, []);

  const resetUiToIdle = useCallback(() => {
    setAgentState("idle");
    setStreamingText("");
    setSpeechTranscript("");
  }, []);

  // Sync UI when HTTP health poll detects backend up/down
  useEffect(() => {
    if (backendStatus === "offline") {
      if (!wasOfflineRef.current) {
        wasOfflineRef.current = true;
        resetUiToOffline();
        pushLog(makeLog("System", "Backend offline — UI reset to standby", "error"));
      }
    } else if (backendStatus === "online") {
      if (wasOfflineRef.current) {
        wasOfflineRef.current = false;
        setAgentState((s) => (s === "offline" ? "idle" : s));
        pushLog(makeLog("System", "Backend online", "success"));
      }
    }
  }, [backendStatus, pushLog, resetUiToOffline]);

  // ── Initialize WebSocket ──────────────────────────────────────────────────
  useEffect(() => {
    const scheduleReconnect = () => {
      if (reconnectTimerRef.current) return;
      reconnectTimerRef.current = setTimeout(() => {
        reconnectTimerRef.current = null;
        connectWs();
      }, 3000);
    };

    const connectWs = () => {
      if (wsRef.current?.readyState === WebSocket.OPEN || wsRef.current?.readyState === WebSocket.CONNECTING) {
        return;
      }

      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onopen = () => {
        pushLog(makeLog("WebSocket", "Connected to Friday core", "success"));
        if (backendStatusRef.current !== "offline") {
          setAgentState((s) => (s === "offline" ? "idle" : s));
        }
      };

      ws.onmessage = (event) => {
        const data = JSON.parse(event.data);

        if (data.state) {
          const next = data.state as AgentState;
          setAgentState(next);
          const stateLabels: Record<string, string> = {
            listening: "Listening for command…",
            idle_listening: "Voice call active — ready for next command",
            thinking: "Processing request…",
            talking: "Generating response…",
            transcribing: "Transcribing audio…",
            idle: "Standby",
          };
          if (data.state !== "idle" && data.state !== "idle_listening") {
            pushLog(makeLog("Core Engine", stateLabels[data.state] || data.state, "info"));
          }
        }

        if (data.type === "wake_word_detected") {
          pushLog(makeLog("STT", "Wake word detected", "success"));
        }

        if (data.type === "system_ready") {
          pushLog(makeLog("System", "All systems online", "success"));
        }

        if (data.type === "reset_complete") {
          setMessages([]);
          setStreamingText("");
          setSpeechTranscript("");
          setAgentState((data.state as AgentState) || "idle");
        }

        if (data.type === "chat") {
          setMessages((prev) => {
            const lastAssistant = [...prev].reverse().find((m) => m.role === "assistant");
            if (lastAssistant?.content === data.text) return prev;
            const newMsg: ChatMessage = {
              id: Math.random().toString(36).substring(7),
              role: data.role || "assistant",
              type: "voice",
              content: data.text,
              time: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false }),
            };
            return [...prev, newMsg];
          });
          pushLog(makeLog("LLM", `Response: "${data.text.slice(0, 50)}${data.text.length > 50 ? "…" : ""}"`, "success"));
        }

        if (data.type === "transcript" || data.type === "transcript_chunk" || data.type === "partial_transcript") {
          if (data.countdown !== undefined && data.countdown > 0) {
            setSpeechTranscript(data.text + ` … (sending in ${data.countdown}s)`);
          } else {
            setSpeechTranscript(data.text);
          }
        }

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

        if (data.type === "transcript_clear") {
          setSpeechTranscript("");
        }

        if (data.type === "agent_step") {
          const status = data.status === "done" || data.status === "stopped" ? "success" : "pending";
          pushLog(makeLog("Agent", `Step ${data.step}: ${data.action}`, status));
        }

        if (data.type === "search_briefing") {
          const briefing: SearchBriefingData = {
            query: data.query || "",
            url: data.url || "",
            sources: Array.isArray(data.sources) ? data.sources : [],
            status: data.status === "searching" ? "searching" : "ready",
          };
          setMessages((prev) => {
            const lastAssistant = [...prev].reverse().find((m) => m.role === "assistant");
            if (lastAssistant) {
              const sameText = data.summary && lastAssistant.content === data.summary;
              const searching = lastAssistant.briefing?.status === "searching" || lastAssistant.isStreaming;
              if (sameText || searching || lastAssistant.briefing?.query === briefing.query) {
                return prev.map((m) =>
                  m.id === lastAssistant.id
                    ? {
                        ...m,
                        content: data.summary || m.content,
                        briefing,
                        isStreaming: data.status === "searching" ? m.isStreaming : false,
                      }
                    : m
                );
              }
            }
            const newMsg: ChatMessage = {
              id: "brief-" + Date.now(),
              role: "assistant",
              type: "voice",
              content: data.summary || `Searching the web for ${briefing.query}…`,
              time: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false }),
              briefing,
            };
            return [...prev, newMsg];
          });
          if (data.status === "searching") {
            pushLog(makeLog("Browser", `Searching: ${data.query}`, "pending"));
          } else {
            pushLog(makeLog("Browser", `Search briefing ready (${(data.sources || []).length} sources)`, "success"));
          }
        }

        if (data.action === "focus_window") {
          window.focus();
          pushLog(makeLog("UI", "Window focus restored", "info"));
        }
      };

      ws.onclose = () => {
        resetUiToOffline();
        pushLog(makeLog("WebSocket", "Connection lost — retrying in 3s", "error"));
        scheduleReconnect();
      };

      ws.onerror = () => ws.close();
    };

    connectWs();

    return () => {
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      wsRef.current?.close();
    };
  }, [pushLog, resetUiToIdle, resetUiToOffline]);

  // ── Send message ──────────────────────────────────────────────────────────
  const sendMessage = useCallback(async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;

    if (!isBackendOnline) {
      resetUiToOffline();
      pushLog(makeLog("Chat", "Backend offline — message not sent", "error"));
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

    chatAbortRef.current?.abort();
    const controller = new AbortController();
    chatAbortRef.current = controller;

    const assistantMsgId = "assistant-" + Date.now();
    let finalText = "";

    try {
      const response = await fetch(`${BACKEND_URL}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: trimmed, id: userMsgId }),
        signal: controller.signal,
      });
      if (!response.ok) throw new Error("HTTP " + response.status);

      const reader = response.body?.getReader();
      if (reader) {
        const decoder = new TextDecoder();
        let buffer = "";
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";
          for (const line of lines) {
            if (!line.startsWith("data: ")) continue;
            try {
              const payload = JSON.parse(line.slice(6));
              if (payload.text) {
                finalText += payload.text;
                const currentContent = finalText;
                setStreamingText(currentContent);
                setAgentState("talking");
                setMessages((prev) => {
                  const exists = prev.some((m) => m.id === assistantMsgId);
                  const lastAssistant = [...prev].reverse().find((m) => m.role === "assistant");
                  if (
                    !exists &&
                    lastAssistant &&
                    lastAssistant.content === currentContent &&
                    lastAssistant.id !== assistantMsgId
                  ) {
                    return prev;
                  }
                  const assistantMsg: ChatMessage = {
                    id: assistantMsgId,
                    role: "assistant",
                    type: "text",
                    content: currentContent,
                    isStreaming: true,
                    time: new Date().toLocaleTimeString([], {
                      hour: "2-digit",
                      minute: "2-digit",
                      hour12: false,
                    }),
                    briefing: prev.find((m) => m.id === assistantMsgId)?.briefing,
                  };
                  if (!exists) return [...prev, assistantMsg];
                  return prev.map((m) =>
                    m.id === assistantMsgId ? { ...m, content: currentContent } : m
                  );
                });
              }
              if (payload.done) {
                setStreamingText("");
                setAgentState("idle");
                if (finalText) {
                  setMessages((prev) =>
                    prev.map((m) =>
                      m.id === assistantMsgId
                        ? { ...m, content: finalText, isStreaming: false }
                        : m
                    )
                  );
                } else {
                  setMessages((prev) => prev.filter((m) => m.id !== assistantMsgId));
                }
              }
              if (payload.error) {
                pushLog(makeLog("Chat", `Error: ${payload.error}`, "error"));
                setAgentState("idle");
                setMessages((prev) => prev.filter((m) => m.id !== assistantMsgId));
              }
            } catch {
              // ignore malformed SSE chunks
            }
          }
        }
        setStreamingText("");
        setAgentState((s) => (s === "thinking" || s === "talking" ? "idle" : s));
      }
    } catch (err) {
      if (controller.signal.aborted) return;
      pushLog(makeLog("Chat", `Error: ${String(err)}`, "error"));
      setAgentState("idle");
      resetUiToOffline();
    } finally {
      if (chatAbortRef.current === controller) {
        chatAbortRef.current = null;
      }
    }
  }, [isBackendOnline, pushLog, resetUiToOffline]);

  // ── Mic Toggle ────────────────────────────────────────────────────────────
  const toggleMic = useCallback(async () => {
    if (!isBackendOnline) {
      resetUiToOffline();
      pushLog(makeLog("Voice", "Backend offline — voice mode unavailable", "error"));
      return;
    }

    try {
      const inVoiceSession =
        agentState !== "idle" && agentState !== "offline";

      if (inVoiceSession) {
        await fetch(`${BACKEND_URL}/stop-trigger`, { method: "POST" });
        resetUiToIdle();
        pushLog(makeLog("Voice", "Stop trigger sent", "info"));
        return;
      }

      const response = await fetch(`${BACKEND_URL}/listen-trigger`, { method: "POST" });
      if (response.ok) {
        setAgentState("listening");
        pushLog(makeLog("Voice", "Listen trigger activated", "success"));
      } else {
        throw new Error("HTTP " + response.status);
      }
    } catch (err) {
      pushLog(makeLog("Voice", `Mic error: ${String(err)}`, "error"));
      resetUiToOffline();
    }
  }, [agentState, isBackendOnline, pushLog, resetUiToIdle, resetUiToOffline]);

  const clearChat = useCallback(async () => {
    chatAbortRef.current?.abort();
    chatAbortRef.current = null;

    setMessages([]);
    setStreamingText("");
    setSpeechTranscript("");
    setAgentState("idle");

    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ event: "stop" }));
    }

    if (!isBackendOnline) {
      pushLog(makeLog("UI", "Chat cleared locally — backend offline", "info"));
      return;
    }

    try {
      pushLog(makeLog("UI", "Stopping voice, speech, and all tasks…", "info"));
      const response = await fetch(`${BACKEND_URL}/reset`, { method: "POST" });
      if (!response.ok) {
        throw new Error(`Server returned ${response.status}`);
      }
      const data = await response.json();
      const stopped = data.cancelled_tasks ?? 0;
      pushLog(
        makeLog(
          "UI",
          stopped > 0
            ? `Chat refreshed — ${stopped} background task(s) stopped`
            : "Chat refreshed — voice and speech stopped",
          "success"
        )
      );
    } catch (err) {
      pushLog(makeLog("UI", `Reset failed: ${String(err)}`, "error"));
      resetUiToOffline();
    }
  }, [isBackendOnline, pushLog, resetUiToOffline]);

  return {
    messages,
    inputText,
    setInputText,
    settingsOpen,
    setSettingsOpen,
    isListening: agentState === "listening" || agentState === "idle_listening",
    isSpeaking: agentState === "talking",
    streamingText,
    speechTranscript,
    agentState,
    backendStatus,
    backendLatency,
    isBackendOnline,
    actionLogs,
    sendMessage,
    toggleMic,
    clearChat,
    lastSentence: messages.filter((m) => m.role === "assistant").slice(-1)[0]?.content || "",
  };
}