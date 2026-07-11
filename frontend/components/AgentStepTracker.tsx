import { Terminal, CheckCircle2, Loader2, XCircle } from "lucide-react";
import { ActionLogEntry } from "@/hooks/useFriday";

export default function AgentStepTracker({ logs }: { logs: ActionLogEntry[] }) {
  const agentLogs = logs.filter((log) => log.source === "Agent");
  
  if (agentLogs.length === 0) return null;

  return (
    <div className="flex flex-col gap-2 p-4 pt-1 border-b border-white/5 dark:border-white/10 bg-black/10 dark:bg-black/30">
      <div className="flex items-center gap-2 text-xs font-semibold text-emerald-400 mb-1">
        <Terminal className="w-4 h-4 animate-pulse" />
        AUTONOMOUS EXECUTION
      </div>
      <div className="flex flex-col gap-1.5 overflow-hidden">
        {agentLogs.slice(0, 4).map((log, i) => (
          <div key={log.id} className="flex flex-row items-center gap-3 text-sm">
            {log.status === "pending" ? (
              <Loader2 className="w-4 h-4 text-blue-400 animate-spin" />
            ) : log.status === "error" ? (
              <XCircle className="w-4 h-4 text-red-500" />
            ) : (
              <CheckCircle2 className="w-4 h-4 text-emerald-500" />
            )}
            <span className={`font-mono text-xs ${log.status === 'success' ? 'text-zinc-500 dark:text-white/40' : 'text-zinc-900 dark:text-white/80'}`}>
              {log.action}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
