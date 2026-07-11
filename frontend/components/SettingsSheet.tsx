"use client";

import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  Bot,
  Volume2,
  Zap,
  Shield,
  Moon,
  Sun,
  Layout,
  Loader2,
  Check,
} from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { Slider } from "@/components/ui/slider";
import { Separator } from "@/components/ui/separator";
import { useState, useEffect, useCallback, useRef } from "react";
import { BACKEND_URL } from "@/lib/api";

interface Settings {
  autoWake: boolean;
  realTimeFeedback: boolean;
  volume: number;
  confidence: number;
  theme: "light" | "dark";
  muted: boolean;
}

const DEFAULTS: Settings = {
  autoWake: true,
  realTimeFeedback: true,
  volume: 80,
  confidence: 85,
  theme: "light",
  muted: false,
};

interface SettingsSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export default function SettingsSheet({ open, onOpenChange }: SettingsSheetProps) {
  const [settings, setSettings] = useState<Settings>(DEFAULTS);
  const [loading, setLoading] = useState(false);
  const [saved, setSaved] = useState(false);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  /* ── Load settings from backend when sheet opens ── */
  useEffect(() => {
    if (!open) return;
    setLoading(true);
    fetch(`${BACKEND_URL}/settings`)
      .then((r) => r.json())
      .then((data) => {
        setSettings({ ...DEFAULTS, ...data });
        if (data.theme === "dark") {
          document.documentElement.classList.add("dark");
        } else {
          document.documentElement.classList.remove("dark");
        }
      })
      .catch(() => {/* keep defaults on error */})
      .finally(() => setLoading(false));
  }, [open]);

  /* ── Update DOM theme class when settings.theme changes ── */
  useEffect(() => {
    if (settings.theme === "dark") {
      document.documentElement.classList.add("dark");
    } else {
      document.documentElement.classList.remove("dark");
    }
  }, [settings.theme]);

  /* ── Debounced save to backend ── */
  const persist = useCallback((patch: Partial<Settings>) => {
    setSettings((prev) => {
      const next = { ...prev, ...patch };
      if (saveTimer.current) clearTimeout(saveTimer.current);
      saveTimer.current = setTimeout(async () => {
        try {
          await fetch(`${BACKEND_URL}/settings`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(next),
          });
          setSaved(true);
          setTimeout(() => setSaved(false), 2000);
        } catch {/* ignore network errors */}
      }, 500);
      return next;
    });
  }, []);

  const s = settings;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-[400px] sm:w-[540px] glass border-l-white/20 dark:border-l-zinc-700/20 p-0 overflow-hidden outline-none">
        <div className="h-full flex flex-col bg-white/40 dark:bg-zinc-900/80 backdrop-blur-3xl p-8">
          {/* Header */}
          <SheetHeader className="mb-8 items-start">
            <div className="p-3 rounded-2xl bg-primary text-primary-foreground mb-4 shadow-lg shadow-primary/20">
              <Zap className="w-5 h-5" />
            </div>
            <div className="flex items-center gap-3 w-full">
              <SheetTitle className="text-2xl font-bold tracking-tight flex-1">
                Core Settings
              </SheetTitle>
              {loading && <Loader2 className="w-4 h-4 animate-spin text-zinc-400" />}
              {saved && !loading && (
                <span className="flex items-center gap-1 text-[10px] text-emerald-600 font-jetbrains">
                  <Check className="w-3 h-3" /> Saved
                </span>
              )}
            </div>
            <SheetDescription className="text-zinc-500 dark:text-zinc-400 font-inter">
              Configure F.R.I.D.A.Y parameters and interface preferences.
            </SheetDescription>
          </SheetHeader>

          <div className="flex-1 overflow-y-auto pr-2 space-y-10 scrollbar-hide">

            {/* ── NEURAL LINK ── */}
            <section className="space-y-4">
              <div className="flex items-center gap-2 mb-2">
                <Bot className="w-4 h-4 text-primary" />
                <h3 className="text-sm font-bold uppercase tracking-widest text-zinc-900 dark:text-zinc-100">Neural Link</h3>
              </div>
              <div className="grid gap-6 p-5 rounded-3xl bg-white/40 dark:bg-zinc-800/40 border border-white/60 dark:border-zinc-700/60 shadow-sm">
                <div className="flex items-center justify-between">
                  <div className="space-y-0.5">
                    <p className="text-sm font-medium dark:text-zinc-200">Auto-Wake Detection</p>
                    <p className="text-xs text-muted-foreground">Wake Friday with "Hey Friday"</p>
                  </div>
                  <Switch
                    checked={s.autoWake}
                    onCheckedChange={(v) => persist({ autoWake: v })}
                  />
                </div>
                <Separator className="bg-zinc-200/40 dark:bg-zinc-700/40" />
                <div className="flex items-center justify-between">
                  <div className="space-y-0.5">
                    <p className="text-sm font-medium dark:text-zinc-200">Real-time Feedback</p>
                    <p className="text-xs text-muted-foreground">Show transcripts while speaking</p>
                  </div>
                  <Switch
                    checked={s.realTimeFeedback}
                    onCheckedChange={(v) => persist({ realTimeFeedback: v })}
                  />
                </div>
                <Separator className="bg-zinc-200/40 dark:bg-zinc-700/40" />
                <div className="flex items-center justify-between">
                  <div className="space-y-0.5">
                    <p className="text-sm font-medium dark:text-zinc-200">Mute Audio Output</p>
                    <p className="text-xs text-muted-foreground">Silence Friday voice responses</p>
                  </div>
                  <Switch
                    checked={s.muted}
                    onCheckedChange={(v) => persist({ muted: v })}
                  />
                </div>
              </div>
            </section>

            {/* ── AURAL CORE ── */}
            <section className="space-y-4">
              <div className="flex items-center gap-2 mb-2">
                <Volume2 className="w-4 h-4 text-primary" />
                <h3 className="text-sm font-bold uppercase tracking-widest text-zinc-900 dark:text-zinc-100">Aural Core</h3>
              </div>
              <div className="space-y-8 p-5 rounded-3xl bg-white/40 dark:bg-zinc-800/40 border border-white/60 dark:border-zinc-700/60 shadow-sm">
                <div className="space-y-4">
                  <div className="flex justify-between items-center">
                    <p className="text-sm font-medium dark:text-zinc-200">Response Volume</p>
                    <span className="text-xs font-jetbrains text-zinc-400">{s.volume}%</span>
                  </div>
                  <Slider
                    value={[s.volume]}
                    onValueChange={(v) => persist({ volume: (v as number[])[0] })}
                    onValueCommitted={(v) => persist({ volume: Array.isArray(v) ? v[0] : v })}
                    max={100}
                    step={1}
                    className="**:[[role=slider]]:bg-primary **:[[role=slider]]:border-primary"
                  />
                </div>
                <div className="space-y-4">
                  <div className="flex justify-between items-center">
                    <p className="text-sm font-medium dark:text-zinc-200">Agent Confidence Threshold</p>
                    <span className="text-xs font-jetbrains text-zinc-400">{(s.confidence / 100).toFixed(2)}</span>
                  </div>
                  <Slider
                    value={[s.confidence]}
                    onValueChange={(v) => persist({ confidence: (v as number[])[0] })}
                    onValueCommitted={(v) => persist({ confidence: Array.isArray(v) ? v[0] : v })}
                    max={100}
                    step={1}
                  />
                </div>
              </div>
            </section>

            {/* ── INTERFACE ── */}
            <section className="space-y-4">
              <div className="flex items-center gap-2 mb-2">
                <Layout className="w-4 h-4 text-primary" />
                <h3 className="text-sm font-bold uppercase tracking-widest text-zinc-900 dark:text-zinc-100">Interface</h3>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <button
                  onClick={() => persist({ theme: "light" })}
                  className={`flex flex-col items-center gap-3 p-4 rounded-3xl transition-all ${
                    s.theme === "light"
                      ? "bg-white dark:bg-zinc-800 border-2 border-primary dark:border-primary shadow-md"
                      : "bg-white/20 dark:bg-zinc-800/20 border-2 border-transparent hover:border-zinc-200 dark:hover:border-zinc-700 opacity-50"
                  }`}
                >
                  <Sun className={`w-5 h-5 ${s.theme === "light" ? "text-primary" : "text-zinc-600 dark:text-zinc-400"}`} />
                  <span className={`text-xs font-bold font-inter ${s.theme === "light" ? "text-zinc-900 dark:text-zinc-100" : "text-zinc-500 dark:text-zinc-400"}`}>
                    Light Pro
                  </span>
                </button>
                <button
                  onClick={() => persist({ theme: "dark" })}
                  className={`flex flex-col items-center gap-3 p-4 rounded-3xl transition-all ${
                    s.theme === "dark"
                      ? "bg-white dark:bg-zinc-800 border-2 border-primary dark:border-primary shadow-md"
                      : "bg-white/20 dark:bg-zinc-800/20 border-2 border-transparent hover:border-zinc-200 dark:hover:border-zinc-700 opacity-50"
                  }`}
                >
                  <Moon className={`w-5 h-5 ${s.theme === "dark" ? "text-primary" : "text-zinc-400"}`} />
                  <span className={`text-xs font-bold font-inter ${s.theme === "dark" ? "text-zinc-900 dark:text-zinc-100" : "text-zinc-400 dark:text-zinc-500"}`}>
                    Dark Ops
                  </span>
                </button>
              </div>
            </section>

            {/* ── SECURITY ── */}
            <section className="space-y-4 pb-10">
              <div className="flex items-center gap-2 mb-2">
                <Shield className="w-4 h-4 text-emerald-500" />
                <h3 className="text-sm font-bold uppercase tracking-widest text-zinc-900 dark:text-zinc-100">Security</h3>
              </div>
              <div className="p-5 rounded-3xl bg-emerald-50/50 dark:bg-emerald-950/30 border border-emerald-100 dark:border-emerald-900/50 flex items-center justify-between">
                <div className="space-y-0.5">
                  <p className="text-sm font-medium text-emerald-900 dark:text-emerald-200">End-to-End Encryption</p>
                  <p className="text-xs text-emerald-600/70 dark:text-emerald-400/70">Neural link is fully encrypted</p>
                </div>
                <div className="p-1 rounded-full bg-emerald-500 text-primary-foreground">
                  <Shield className="w-4 h-4" />
                </div>
              </div>
            </section>
          </div>

          <div className="mt-auto pt-6 border-t border-zinc-200/40 dark:border-zinc-700/40">
            <div className="flex items-center justify-between text-[11px] text-zinc-400 font-jetbrains">
              <span>FRIDAY_v2.4.0</span>
              <span>SYSTEM_READY</span>
            </div>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}
