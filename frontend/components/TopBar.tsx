"use client";

import { useState } from "react";
import { Settings2, Globe, RotateCcw, Wifi, WifiOff, Loader2 } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
} from "@/components/ui/dropdown-menu";
import { useBackendStatus, BackendStatus } from "@/hooks/useBackendStatus";

interface TopBarProps {
  onSettingsClick: () => void;
  onRefreshChat: () => void;
  backendStatus?: BackendStatus;
  backendLatency?: number | null;
}

/* Shared icon-button class — applied directly to native <button> elements */
const iconBtn =
  "inline-flex items-center justify-center rounded-xl h-9 w-9 bg-white/40 dark:bg-zinc-800/40 hover:bg-white/80 dark:hover:bg-zinc-700/80 border border-white/60 dark:border-zinc-700/60 shadow-sm transition-all active:scale-95 cursor-pointer outline-none focus-visible:ring-2 focus-visible:ring-primary/40";

export default function TopBar({
  onSettingsClick,
  onRefreshChat,
  backendStatus: backendStatusProp,
  backendLatency: backendLatencyProp,
}: TopBarProps) {
  const polled = useBackendStatus(backendStatusProp ? 60000 : 5000);
  const status = backendStatusProp ?? polled.status;
  const latency = backendLatencyProp ?? polled.latency;
  const [networkOpen, setNetworkOpen] = useState(false);

  const isOnline   = status === "online";
  const isChecking = status === "checking";
  const isOffline  = status === "offline";

  /* ── badge colours ── */
  const badgeCls = isChecking
    ? "border-amber-200/70 dark:border-amber-800/50 bg-amber-50/60 dark:bg-amber-950/40 text-amber-700 dark:text-amber-300"
    : isOnline
    ? "border-emerald-200/70 dark:border-emerald-800/50 bg-emerald-50/60 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-300"
    : "border-red-200/70 dark:border-red-800/50 bg-red-50/60 dark:bg-red-950/40 text-red-600 dark:text-red-300";

  const dotCls = isChecking
    ? "bg-amber-400 animate-pulse"
    : isOnline
    ? "bg-emerald-500 animate-pulse"
    : "bg-red-500";

  const badgeLabel = isChecking ? "CHECKING…" : isOnline ? "BACKEND ONLINE" : "BACKEND OFFLINE";

  const StatusIcon = isChecking ? Loader2 : isOnline ? Wifi : WifiOff;

  return (
    <header className="h-14 flex-shrink-0 flex items-center px-6 relative z-50 glass rounded-3xl shadow-sm">

      {/* ── LEFT — System status + Refresh ── */}
      <div className="flex items-center gap-6">
        <div className="flex flex-col">
          <div className="flex items-center gap-1.5">
            <div className={`w-1.5 h-1.5 rounded-full ${isChecking ? "bg-amber-400 animate-pulse" : isOnline ? "bg-emerald-500 animate-pulse" : "bg-red-500"}`} />
            <span className="text-[10px] font-bold tracking-[0.15em] text-zinc-900 dark:text-zinc-100 uppercase">
              {isOffline ? "Core Offline" : "System Core"}
            </span>
          </div>
          <span className="text-[9px] text-muted-foreground font-jetbrains mt-0.5">
            HYDERABAD_NODE_SC-1
          </span>
        </div>

        <div className="h-6 w-px bg-zinc-200/50 dark:bg-zinc-700/50" />

        <div className="hidden md:flex items-center">
          {/* Plain <button> — no nesting issues */}
          <button
            onClick={onRefreshChat}
            className="inline-flex items-center gap-2 rounded-lg h-7 px-3 bg-zinc-100 dark:bg-zinc-800 hover:bg-zinc-200 dark:hover:bg-zinc-700 border border-zinc-200/60 dark:border-zinc-700/60 shadow-sm transition-all active:scale-95 text-zinc-600 dark:text-zinc-400 font-jetbrains text-[9px] uppercase tracking-wide cursor-pointer outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
          >
            <RotateCcw className="w-3 h-3 text-zinc-500 dark:text-zinc-400" />
            Refresh Chat
          </button>
        </div>
      </div>

      {/* ── CENTER — Logo ── */}
      <div className="absolute left-1/2 -translate-x-1/2 flex flex-col items-center pointer-events-none">
        <h1 className="text-lg font-bold tracking-[0.4em] text-zinc-900 dark:text-zinc-100 filter drop-shadow-sm">
          <span className="opacity-40">F.R.I.D.A.Y.</span>
        </h1>
      </div>

      {/* ── RIGHT — Status badge + buttons ── */}
      <div className="ml-auto flex items-center gap-3">

        {/* Live backend status badge */}
        <div className="hidden sm:flex items-center gap-2 mr-1">
          <div
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-[9px] font-jetbrains font-bold tracking-wider transition-all ${badgeCls}`}
          >
            <div className={`w-1.5 h-1.5 rounded-full ${dotCls}`} />
            {badgeLabel}
          </div>
        </div>

        {/* ── Settings button — plain <button>, no nesting ── */}
        <button
          onClick={onSettingsClick}
          className={iconBtn}
          title="Settings"
          aria-label="Open settings"
        >
          <Settings2 className="w-4 h-4 text-zinc-700 dark:text-zinc-300" />
        </button>

        {/* ── Network dropdown — Trigger IS the button, no wrapper ── */}
        <DropdownMenu open={networkOpen} onOpenChange={setNetworkOpen}>
          {/*
            DropdownMenuTrigger from @base-ui/react already renders a <button>.
            We style it directly — no extra Button wrapper to avoid nesting.
          */}
          <DropdownMenuTrigger
            className={`${iconBtn} relative`}
            aria-label="Network status"
            title="Network status"
          >
            <Globe className="w-4 h-4 text-zinc-700 dark:text-zinc-300" />
            {/* tiny status dot */}
            <span
              className={`absolute top-1.5 right-1.5 w-1.5 h-1.5 rounded-full border border-white dark:border-zinc-700 ${
                isChecking ? "bg-amber-400" : isOnline ? "bg-emerald-500" : "bg-red-500"
              }`}
            />
          </DropdownMenuTrigger>

          <DropdownMenuContent
            align="end"
            sideOffset={8}
            className="w-64 bg-white/80 dark:bg-zinc-900/90 backdrop-blur-3xl border border-white/50 dark:border-zinc-700/50 rounded-3xl shadow-2xl p-0 overflow-hidden"
          >
            {/* Header stripe */}
            <div
              className={`flex items-center gap-2.5 px-5 py-4 border-b ${
                isOnline
                  ? "border-emerald-100 dark:border-emerald-900/50 bg-emerald-50/40 dark:bg-emerald-950/30"
                  : isChecking
                  ? "border-amber-100 dark:border-amber-900/50 bg-amber-50/40 dark:bg-amber-950/30"
                  : "border-red-100 dark:border-red-900/50 bg-red-50/40 dark:bg-red-950/30"
              }`}
            >
              <StatusIcon
                className={`w-4 h-4 ${
                  isChecking
                    ? "animate-spin text-amber-500 dark:text-amber-400"
                    : isOnline
                    ? "text-emerald-600 dark:text-emerald-400"
                    : "text-red-500 dark:text-red-400"
                }`}
              />
              <span className="text-xs font-bold tracking-widest uppercase text-zinc-900 dark:text-zinc-100">
                Network Status
              </span>
              <div className={`ml-auto w-2 h-2 rounded-full ${dotCls}`} />
            </div>

            {/* Stats */}
            <div className="px-5 py-4 space-y-3">
              <StatRow
                label="Backend"
                value={isChecking ? "—" : isOnline ? "ONLINE" : "OFFLINE"}
                  valueClass={
                    isOnline
                      ? "text-emerald-600 dark:text-emerald-400"
                      : isChecking
                      ? "text-amber-500 dark:text-amber-400"
                      : "text-red-500 dark:text-red-400"
                  }
              />
              <StatRow label="Node" value="HYDERABAD_SC-1" />
              <StatRow
                label="Latency"
                value={latency !== null ? `${latency} ms` : "—"}
                valueClass={
                  latency !== null && latency < 100
                    ? "text-emerald-600"
                    : latency !== null
                    ? "text-amber-500"
                    : "text-zinc-400"
                }
              />
              <StatRow label="Poll Interval" value="5 s" />
            </div>

            {/* Close footer */}
            <div className="px-5 pb-4">
              <button
                onClick={() => setNetworkOpen(false)}
                className="w-full text-center text-[10px] font-jetbrains text-zinc-400 hover:text-zinc-600 transition-colors py-1 cursor-pointer"
              >
                Close
              </button>
            </div>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}

function StatRow({
  label,
  value,
  valueClass = "text-zinc-800 dark:text-zinc-200",
}: {
  label: string;
  value: string;
  valueClass?: string;
}) {
  return (
    <div                 className="flex items-center justify-between text-xs font-jetbrains">
      <span className="text-zinc-500 dark:text-zinc-400">{label}</span>
      <span className={`font-semibold ${valueClass}`}>{value}</span>
    </div>
  );
}
