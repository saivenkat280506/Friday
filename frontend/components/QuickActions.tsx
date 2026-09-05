"use client";

import { MessageSquareDashed, ShieldAlert, Zap, Trash2, Cpu } from "lucide-react";

interface QuickActionsProps {
  onSendMessage: (text: string) => void;
  onClearChat: () => void;
}

export default function QuickActions({ onSendMessage, onClearChat }: QuickActionsProps) {
  const actions = [
    {
      label: "Introduce Yourself",
      icon: MessageSquareDashed,
      action: () => onSendMessage("Hello Friday, introduce yourself in one short sentence."),
      color: "text-blue-500 dark:text-blue-400",
      bg: "bg-blue-50 dark:bg-blue-950/40",
    },
    {
      label: "What Time Is It?",
      icon: Cpu,
      action: () => onSendMessage("What time is it right now?"),
      color: "text-emerald-500 dark:text-emerald-400",
      bg: "bg-emerald-50 dark:bg-emerald-950/40",
    },
    {
      label: "Open TextEdit",
      icon: Zap,
      action: () => onSendMessage("Open TextEdit."),
      color: "text-violet-500 dark:text-violet-400",
      bg: "bg-violet-50 dark:bg-violet-950/40",
    },
    {
      label: "Tell a Joke",
      icon: ShieldAlert,
      action: () => onSendMessage("Tell me a short witty joke."),
      color: "text-amber-500 dark:text-amber-400",
      bg: "bg-amber-50 dark:bg-amber-950/40",
    },
    {
      label: "Clear Chat",
      icon: Trash2,
      action: onClearChat,
      color: "text-rose-500 dark:text-rose-400",
      bg: "bg-rose-50 dark:bg-rose-950/40",
    },
  ];

  return (
    <div className="bg-white/40 dark:bg-zinc-900/40 border-b border-border/20 px-6 py-4 backdrop-blur-sm flex-shrink-0">
      <div className="flex items-center gap-2 mb-3">
        <Zap className="w-3.5 h-3.5 text-muted-foreground" />
        <span className="text-[10px] font-bold tracking-[0.2em] text-muted-foreground uppercase mt-0.5">
          Suggested Actions
        </span>
      </div>

      <div className="flex gap-3 overflow-x-auto pb-1 scrollbar-hide">
        {actions.map((act, idx) => {
          const Icon = act.icon;
          return (
            <button
              key={idx}
              onClick={act.action}
              className="group flex-shrink-0 flex items-center gap-2.5 px-3 py-1.5 rounded-xl bg-white/50 dark:bg-zinc-800/50 border border-white/80 dark:border-zinc-700/80 shadow-sm hover:bg-white dark:hover:bg-zinc-800 hover:shadow-md transition-all active:scale-95 cursor-pointer outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
            >
              <div className={`p-1 rounded-md ${act.bg} transition-colors group-hover:bg-opacity-80`}>
                <Icon className={`w-3.5 h-3.5 ${act.color}`} />
              </div>
              <span className="text-[11px] font-medium text-zinc-700 dark:text-zinc-300 leading-tight">
                {act.label}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
