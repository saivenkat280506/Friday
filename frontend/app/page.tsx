"use client";
// F.R.I.D.A.Y. workspace

import { useEffect } from "react";
import TopBar from "@/components/TopBar";
import LeftPanel from "@/components/LeftPanel";
import ChatArea from "@/components/ChatArea";
import QuickActions from "@/components/QuickActions";
import AgentStepTracker from "@/components/AgentStepTracker";
import SettingsSheet from "@/components/SettingsSheet";
import { useFriday } from "@/hooks/useFriday";

export default function FridayWorkspace() {
  const friday = useFriday();

  // Load and apply initial theme settings on startup
  useEffect(() => {
    fetch("http://127.0.0.1:8000/settings")
      .then((r) => r.json())
      .then((data) => {
        if (data && data.theme === "dark") {
          document.documentElement.classList.add("dark");
        } else {
          document.documentElement.classList.remove("dark");
        }
      })
      .catch(() => {});
  }, []);

  return (
    <main className="relative flex h-screen w-screen bg-background overflow-hidden p-4 gap-4">
      {/* Texture Layer */}
      <div className="noise" />

      {/* LEFT PANEL - Glassy & Floating */}
      <LeftPanel 
        agentState={friday.agentState} 
        isListening={friday.isListening}
        isBackendOnline={friday.isBackendOnline}
        toggleMic={friday.toggleMic}
        speechTranscript={friday.speechTranscript}
        onSettingsClick={() => friday.setSettingsOpen(true)}
      />

      {/* MAIN WORKSPACE - Unified Chat & Actions */}
      <div className="relative flex flex-col flex-1 gap-4 min-w-0">
        <TopBar 
          onSettingsClick={() => friday.setSettingsOpen(true)} 
          onRefreshChat={friday.clearChat}
          backendStatus={friday.backendStatus}
          backendLatency={friday.backendLatency}
        />
        
        <div className="flex-1 flex gap-4 min-h-0">
          <div className="flex-1 flex flex-col glass rounded-3xl overflow-hidden shadow-sm">
            <QuickActions 
              onSendMessage={friday.sendMessage} 
              onClearChat={friday.clearChat} 
            />
            <AgentStepTracker logs={friday.actionLogs} />
            <ChatArea 
              messages={friday.messages} 
              inputText={friday.inputText}
              setInputText={friday.setInputText}
              sendMessage={friday.sendMessage}
              streamingText={friday.streamingText}
              speechTranscript={friday.speechTranscript}
              agentState={friday.agentState ?? "idle"}
              isBackendOnline={friday.isBackendOnline}
              toggleMic={friday.toggleMic}
            />
          </div>
        </div>
      </div>

      <SettingsSheet 
        open={friday.settingsOpen} 
        onOpenChange={friday.setSettingsOpen} 
      />

    </main>
  );
}
