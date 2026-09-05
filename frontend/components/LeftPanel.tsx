"use client";

import { useEffect, useState } from "react";
import { Orb, AgentState } from "@/components/ui/orb";
import { Waveform } from "@/components/ui/waveform";
import { Mic, MicOff, Settings2, ShieldCheck, WifiOff, Zap } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface LeftPanelProps {
  agentState: AgentState;
  isListening: boolean;
  isBackendOnline?: boolean;
  toggleMic: () => void;
  speechTranscript: string;
  onSettingsClick: () => void;
}

export default function LeftPanel({ 
  agentState, 
  isListening,
  isBackendOnline = true,
  toggleMic,
  speechTranscript,
  onSettingsClick
}: LeftPanelProps) {
  const [isDark, setIsDark] = useState(false);

  useEffect(() => {
    const check = () => setIsDark(document.documentElement.classList.contains("dark"));
    check();
    const observer = new MutationObserver(check);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);

  const isOffline = agentState === "offline" || !isBackendOnline;
  const isActive = !isOffline && agentState !== "idle";

  const waveformData = isActive
    ? Array.from({ length: 40 }, () => {
        const base = agentState === "talking" ? 0.6 : 0.3;
        const variance = agentState === "talking" ? 0.4 : 0.2;
        return Math.random() * variance + base;
      })
    : [];

  const statusLabel = isOffline
    ? "Backend Offline"
    : agentState === "idle_listening"
    ? "Voice Call Active"
    : "System Active";

  const statusDot = isOffline ? "bg-red-500" : "bg-emerald-500 animate-pulse";

  const subtitle = isOffline
    ? "Backend unreachable — start Friday backend"
    : isListening
    ? "Listening to your request..."
    : agentState === "idle_listening"
    ? "On call — speak your next command"
    : agentState === "talking"
    ? "Speaking..."
    : agentState === "thinking"
    ? "Processing..."
    : agentState === "transcribing"
    ? "Transcribing..."
    : "Click to start voice call";

  return (
    <aside className="w-[320px] glass rounded-3xl flex flex-col h-full shadow-sm overflow-hidden p-6 gap-8">
      {/* HEADER SECTION */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className={cn("w-2 h-2 rounded-full", statusDot)} />
          <span className={cn(
            "text-[11px] font-bold tracking-widest uppercase",
            isOffline ? "text-red-500/80" : "text-muted-foreground"
          )}>
            {statusLabel}
          </span>
        </div>
        <Button variant="ghost" size="icon" onClick={onSettingsClick} className="rounded-full hover:bg-white/50 dark:hover:bg-zinc-800/50">
          <Settings2 className="w-4 h-4 text-muted-foreground" />
        </Button>
      </div>

      {/* ORB AREA - Centered & Floating */}
      <div className="flex-1 flex flex-col items-center justify-center gap-6">
        <div className={cn(
          "relative w-[168px] h-[168px] flex items-center justify-center rounded-full transition-all duration-700",
          "bg-white shadow-[0_0_60px_rgba(255,255,255,0.9),inset_0_0_30px_rgba(0,0,0,0.02)]",
          "dark:bg-zinc-900 dark:shadow-[0_0_60px_rgba(0,0,0,0.5),inset_0_0_30px_rgba(255,255,255,0.02)]",
          "orb-float",
          agentState === "talking" && "ring-4 ring-blue-50 dark:ring-blue-900/50 ring-offset-4"
        )}>
          <Orb
            agentState={agentState}
            className="w-full h-full p-2"
            colors={isDark ? ["#1a3a5c", "#2d5a8e"] : ["#88A9D1", "#5C84B1"]}
          />
        </div>

        <div className="text-center space-y-1">
          <h2 className="text-xl font-medium tracking-tight">F.R.I.D.A.Y.</h2>
          <p className={cn(
            "text-xs font-inter italic px-4",
            isOffline ? "text-red-500/70" : "text-muted-foreground"
          )}>
            {subtitle}
          </p>
        </div>

        {/* INTEGRATED WAVEFORM */}
        <div className="w-full h-12 flex items-center justify-center px-4">
          <Waveform 
            data={waveformData} 
            barWidth={3} 
            barGap={2} 
            barRadius={10}
            fadeEdges={true}
            height={40}
            className="w-full h-full opacity-60"
            barColor={isDark ? "#3a6a9e" : "#5C84B1"}
          />
        </div>
      </div>

      {/* FOOTER SECTION - Glass Control */}
      <div className="flex flex-col gap-4">
        {(speechTranscript || agentState === "thinking" || agentState === "transcribing") && (
          <div className="p-3 bg-white/40 dark:bg-zinc-800/40 border border-white/60 dark:border-zinc-700/60 rounded-2xl animate-in fade-in slide-in-from-bottom-2">
            <p className="text-[13px] text-foreground leading-snug line-clamp-2 italic font-inter opacity-80">
              {speechTranscript ? `"${speechTranscript.trim()}"` : (
                <span className="flex items-center gap-2">
                  <span className="w-1 h-1 rounded-full bg-primary animate-bounce [animation-delay:-0.3s]" />
                  <span className="w-1 h-1 rounded-full bg-primary animate-bounce [animation-delay:-0.15s]" />
                  <span className="w-1 h-1 rounded-full bg-primary animate-bounce" />
                  {agentState === "transcribing" ? "Transcribing..." : "Processing..."}
                </span>
              )}
            </p>
          </div>
        )}

        {(() => {
          const stateConfig = {
            idle: { label: "Voice Mode", color: "bg-primary hover:bg-zinc-800", icon: Mic },
            idle_listening: { label: "On Call", color: "bg-emerald-600 hover:bg-emerald-700", icon: Mic },
            listening: { label: "Listening", color: "bg-emerald-500 hover:bg-emerald-600 animate-pulse", icon: Mic },
            transcribing: { label: "Transcribing", color: "bg-purple-500 hover:bg-purple-600", icon: Zap },
            thinking: { label: "Thinking", color: "bg-amber-500 hover:bg-amber-600 animate-pulse", icon: Settings2 },
            talking: { label: "Speaking", color: "bg-blue-500 hover:bg-blue-600", icon: Mic },
            offline: { label: "Backend Offline", color: "bg-red-500/80 hover:bg-red-600/80", icon: WifiOff },
          };
          const config = stateConfig[agentState as keyof typeof stateConfig] || stateConfig.idle;
          const inSession = agentState !== "idle" && agentState !== "offline";
          const Icon = inSession ? MicOff : config.icon;
          const label = agentState === "idle" ? "Voice Mode" : config.label;

          return (
            <Button 
              onClick={toggleMic}
              disabled={isOffline}
              className={cn(
                "w-full h-14 rounded-2xl transition-all duration-500 gap-3 border-none shadow-md text-primary-foreground font-medium",
                inSession || isOffline ? config.color : "bg-primary hover:bg-zinc-800 dark:hover:bg-zinc-700",
                isOffline && "opacity-80 cursor-not-allowed"
              )}
            >
              <Icon className={cn("w-5 h-5", agentState === "listening" && "animate-pulse")} />
              <span>{label}</span>
            </Button>
          );
        })()}

        <div className="grid grid-cols-2 gap-2 text-[10px] text-muted-foreground font-jetbrains pt-2">
          <div className="flex items-center gap-1.5 p-2 rounded-xl bg-white/30 dark:bg-zinc-800/30 border border-white/40 dark:border-zinc-700/40">
            <ShieldCheck className="w-3 h-3 text-emerald-500" />
            <span className="dark:text-zinc-300">SECURE</span>
          </div>
          <div className="flex items-center gap-1.5 p-2 rounded-xl bg-white/30 dark:bg-zinc-800/30 border border-white/40 dark:border-zinc-700/40">
            <Zap className="w-3 h-3 text-amber-500" />
            <span className="dark:text-zinc-300">v2.4.0</span>
          </div>
        </div>
      </div>
    </aside>
  );
}
