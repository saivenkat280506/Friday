"use client";

import { ExternalLink, Globe } from "lucide-react";
import { cn } from "@/lib/utils";

export interface SearchSource {
  title: string;
  url: string;
  snippet?: string;
}

export interface SearchBriefingData {
  query: string;
  url?: string;
  sources: SearchSource[];
  status?: "searching" | "ready";
}

function hostOf(url: string) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

export default function SearchBriefing({
  briefing,
  className,
}: {
  briefing: SearchBriefingData;
  className?: string;
}) {
  const sources = briefing.sources?.filter((s) => s.title && s.url) ?? [];

  return (
    <div
      className={cn(
        "mt-3 rounded-2xl border border-cyan-500/25 bg-cyan-500/5 dark:bg-sky-950/40 p-3 space-y-2",
        className
      )}
    >
      <div className="flex items-center gap-2 text-[10px] uppercase tracking-widest text-cyan-700 dark:text-cyan-300/80 font-medium">
        <Globe className="w-3.5 h-3.5" />
        Search briefing
        {briefing.status === "searching" && (
          <span className="normal-case tracking-normal text-muted-foreground">Looking it up…</span>
        )}
      </div>
      <p className="text-[13px] text-foreground/90 font-medium leading-snug">
        {briefing.query}
      </p>
      {sources.length > 0 && (
        <ul className="space-y-1.5">
          {sources.slice(0, 5).map((source) => (
            <li key={source.url}>
              <a
                href={source.url}
                target="_blank"
                rel="noreferrer"
                className="group flex items-start gap-2 rounded-lg px-1.5 py-1 hover:bg-white/40 dark:hover:bg-white/5"
              >
                <ExternalLink className="w-3 h-3 mt-0.5 shrink-0 text-cyan-600 dark:text-cyan-400" />
                <span className="min-w-0">
                  <span className="block text-[12px] leading-snug text-foreground group-hover:underline">
                    {source.title}
                  </span>
                  <span className="block text-[10px] text-muted-foreground font-jetbrains">
                    {hostOf(source.url)}
                  </span>
                </span>
              </a>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
