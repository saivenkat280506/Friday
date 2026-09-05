"use client";

import { useChatScroll } from "@/hooks/useChatScroll";
import { Message, MessageContent, MessageAvatar } from "@/components/ui/message";
import { Response } from "@/components/ui/response";
import {
  TranscriptViewerAudio,
  TranscriptViewerContainer,
  TranscriptViewerPlayPauseButton,
  TranscriptViewerScrubBar,
  TranscriptViewerWords,
} from "@/components/ui/transcript-viewer";
import { SendHorizontal, Mic, FileUp, PauseIcon, PlayIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import SearchBriefing, { SearchBriefingData } from "@/components/SearchBriefing";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  type: "text" | "voice";
  content: string;
  time: string;
  isStreaming?: boolean;
  audioSrc?: string;
  alignment?: any;
  briefing?: SearchBriefingData;
}

export interface ChatAreaProps {
  messages: ChatMessage[];
  inputText: string;
  setInputText: (text: string) => void;
  sendMessage: (text: string) => void;
  streamingText?: string;
  speechTranscript?: string;
  agentState?: string;
  isBackendOnline?: boolean;
  toggleMic?: () => void;
}

export default function ChatArea({ 
  messages, 
  inputText, 
  setInputText, 
  sendMessage,
  streamingText,
  speechTranscript,
  agentState,
  isBackendOnline = true,
  toggleMic
}: ChatAreaProps) {
  const scrollRef = useChatScroll([messages, streamingText, speechTranscript]);
  const isOffline = agentState === "offline" || !isBackendOnline;

  return (
    <div className="flex flex-col flex-1 min-h-0 bg-transparent">
      {isOffline && (
        <div className="mx-6 mt-3 px-4 py-2 rounded-2xl border border-red-200/70 dark:border-red-900/50 bg-red-50/70 dark:bg-red-950/30 text-red-600 dark:text-red-300 text-[11px] font-jetbrains text-center">
          Backend offline — Friday cannot listen, speak, or run tasks until the server is back.
        </div>
      )}
      {/* MESSAGES VIEWPORT */}
      <div 
        ref={scrollRef}
        className="flex-1 overflow-y-auto px-6 py-4 space-y-6 scrollbar-hide"
      >
        <div className="flex flex-col gap-1 pb-4">
          <div className="flex items-center gap-2 px-2 py-4">
            <span className="text-[10px] font-bold text-muted-foreground/60 uppercase tracking-widest">Conversation Log</span>
            <div className="flex-1 h-px bg-border/40" />
          </div>

          {messages.map((msg) => (
            <div key={msg.id} className="msg-enter">
              <Message 
                from={msg.role === "assistant" ? "assistant" : "user"}
                className={msg.role === "assistant" ? "justify-start" : "justify-end"}
              >
                {msg.role === "assistant" && (
                  <MessageAvatar 
                    name="JV" 
                    className="self-end mb-1 bg-white dark:bg-zinc-800 shadow-sm ring-white dark:ring-zinc-700 ring-offset-1 ring-1" 
                  />
                )}
                
                {msg.role === "assistant" && msg.audioSrc && msg.content.length > 180 ? (
                  /* BIG PARAGRAPH TRANSCRIPT VIEWER */
                  <div className="max-w-[85%] w-full">
                    <TranscriptViewerContainer
                      audioSrc={msg.audioSrc}
                      audioType="audio/mpeg"
                      alignment={msg.alignment || { characters: [], characterStartTimesSeconds: [], characterEndTimesSeconds: [] }}
                      className="bg-white/80 dark:bg-zinc-900/80 border border-white/40 dark:border-zinc-700/40 shadow-sm rounded-2xl p-4 backdrop-blur-md"
                    >
                      <TranscriptViewerAudio className="sr-only" />
                      <div className="mb-4">
                         <TranscriptViewerWords className="text-sm !leading-relaxed text-slate-700 dark:text-slate-300" />
                      </div>
                      <div className="flex items-center gap-4 border-t border-slate-100 dark:border-slate-700 pt-3">
                        <TranscriptViewerPlayPauseButton className="h-8 w-8 rounded-full shrink-0">
                          {({ isPlaying }) => isPlaying ? <PauseIcon size={14} /> : <PlayIcon size={14} />}
                        </TranscriptViewerPlayPauseButton>
                        <TranscriptViewerScrubBar className="flex-1" />
                      </div>
                    </TranscriptViewerContainer>
                    {msg.briefing && <SearchBriefing briefing={msg.briefing} />}
                    <span className="text-[9px] mt-1.5 opacity-40 font-jetbrains px-2">
                       {msg.time}
                    </span>
                  </div>
                ) : (
                  <MessageContent 
                    variant="contained" 
                    className={cn(
                      "font-inter leading-relaxed text-[14px] shadow-sm",
                      msg.role === "assistant" 
                        ? "rounded-2xl rounded-bl-none bg-white/80 dark:bg-zinc-900/80 border border-white/40 dark:border-zinc-700/40" 
                        : "rounded-2xl rounded-br-none bg-primary text-primary-foreground dark:bg-sky-950/40 dark:text-cyan-100 dark:border dark:border-cyan-500/30"
                    )}
                  >
                    {msg.isStreaming && msg.role === "assistant" ? (
                      <Response>
                        {streamingText || msg.content}
                      </Response>
                    ) : (
                      msg.content
                    )}
                    {msg.role === "assistant" && msg.briefing && (
                      <SearchBriefing briefing={msg.briefing} />
                    )}
                    <span className={cn(
                      "text-[9px] mt-1.5 opacity-40 font-jetbrains",
                      msg.role === "user" ? "text-right" : "text-left"
                    )}>
                      {msg.time}
                    </span>
                  </MessageContent>
                )}
              </Message>
            </div>
          ))}

          {/* LIVE TRANSCRIPTION BUBBLE (Google STT Style) */}
          {speechTranscript && (
            <div className="msg-enter">
              <Message from="user" className="justify-end opacity-70">
                <MessageContent 
                  variant="contained" 
                  className="rounded-2xl rounded-br-none bg-primary/40 text-primary-foreground dark:bg-sky-950/20 dark:text-cyan-200/70 font-inter text-[14px] italic border border-white/10 dark:border-cyan-500/20"
                >
                  <div className="flex items-center gap-2">
                    <span className="w-1.5 h-1.5 rounded-full bg-primary-foreground animate-pulse" />
                    {speechTranscript}
                  </div>
                </MessageContent>
              </Message>
            </div>
          )}
        </div>
      </div>

      {/* INPUT AREA - High Fidelity Floating Design */}
      <div className="px-6 pb-6 pt-2">
        {/* Status Indicator (Minimal) */}
        {agentState === "listening" && !speechTranscript && (
          <div className="mb-3 px-4 py-1.5 bg-primary/5 border border-primary/10 rounded-full w-fit mx-auto animate-in fade-in slide-in-from-bottom-2">
             <div className="flex items-center gap-2">
                <div className="flex gap-1">
                  <span className="w-1 h-1 rounded-full bg-primary animate-bounce [animation-delay:-0.3s]" />
                  <span className="w-1 h-1 rounded-full bg-primary animate-bounce [animation-delay:-0.15s]" />
                  <span className="w-1 h-1 rounded-full bg-primary animate-bounce" />
                </div>
                <span className="text-[11px] text-primary/60 font-medium uppercase tracking-tighter">Listening</span>
             </div>
          </div>
        )}

        <div className="relative group glass-input">
          <div className="bg-white/60 dark:bg-zinc-900/60 border border-white/40 dark:border-zinc-700/40 shadow-xl rounded-[24px] p-2 flex items-center transition-all duration-300 focus-within:bg-white dark:focus-within:bg-zinc-900 focus-within:shadow-2xl focus-within:border-primary/20 backdrop-blur-md">
            <Button variant="ghost" size="icon" className="rounded-full text-muted-foreground hover:bg-zinc-100 dark:hover:bg-zinc-800 h-10 w-10">
              <FileUp className="w-5 h-5" />
            </Button>
            
            <input
              type="text"
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && !isOffline && sendMessage(inputText)}
              disabled={isOffline}
              placeholder={
                isOffline
                  ? "Backend offline..."
                  : agentState === "listening"
                  ? "Say something..."
                  : "Message Friday..."
              }
              className="flex-1 bg-transparent px-4 py-2 text-[14px] focus:outline-none placeholder:text-muted-foreground/50 font-inter disabled:opacity-50"
            />

            <div className="flex items-center gap-1">
              <Button
                variant="ghost"
                size="icon"
                disabled={isOffline}
                className="rounded-full text-muted-foreground hover:bg-zinc-100 dark:hover:bg-zinc-800 h-10 w-10 disabled:opacity-40"
                onClick={toggleMic}
              >
                <Mic className="w-5 h-5" />
              </Button>
              <Button 
                onClick={() => sendMessage(inputText)}
                disabled={isOffline || !inputText.trim()}
                className="bg-primary text-primary-foreground rounded-full h-10 w-10 p-0 shadow-lg hover:bg-zinc-800 dark:hover:bg-zinc-700 disabled:opacity-30 disabled:hover:bg-primary transition-all active:scale-95"
              >
                <SendHorizontal className="w-5 h-5" />
              </Button>
            </div>
          </div>
        </div>
        <p className="text-[10px] text-center text-muted-foreground/40 mt-3 font-inter">
          Friday v2.4 powered by ElevenLabs & Groq LPU
        </p>
      </div>
    </div>
  );
}
