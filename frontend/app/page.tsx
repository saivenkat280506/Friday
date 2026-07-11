"use client";

import TopBar from "@/components/TopBar";
import LeftPanel from "@/components/LeftPanel";
import ChatArea from "@/components/ChatArea";
import QuickActions from "@/components/QuickActions";
import SettingsSheet from "@/components/SettingsSheet";
import AgentStepTracker from "@/components/AgentStepTracker";
import { useFriday } from "@/hooks/useFriday";

export default function Home() {
  const {
    messages,
    inputText,
    setInputText,
    settingsOpen,
    setSettingsOpen,
    isListening,
    streamingText,
    speechTranscript,
    agentState,
    actionLogs,
    sendMessage,
    toggleMic,
    clearChat,
  } = useFriday();

  return (
    <div className="h-screen w-screen overflow-hidden flex flex-col">
      <TopBar
        onSettingsClick={() => setSettingsOpen(true)}
        onRefreshChat={clearChat}
      />
      <div className="flex-1 flex overflow-hidden px-4 pb-4 gap-4 min-h-0">
        <LeftPanel
          agentState={agentState}
          isListening={isListening}
          toggleMic={toggleMic}
          speechTranscript={speechTranscript || ""}
          onSettingsClick={() => setSettingsOpen(true)}
        />
        <div className="flex-1 flex flex-col min-w-0 glass rounded-3xl overflow-hidden">
          <QuickActions onSendMessage={sendMessage} onClearChat={clearChat} />
          <AgentStepTracker logs={actionLogs} />
          <ChatArea
            messages={messages}
            inputText={inputText}
            setInputText={setInputText}
            sendMessage={sendMessage}
            streamingText={streamingText}
            speechTranscript={speechTranscript}
            agentState={agentState}
            toggleMic={toggleMic}
          />
        </div>
      </div>
      <SettingsSheet open={settingsOpen} onOpenChange={setSettingsOpen} />
    </div>
  );
}
