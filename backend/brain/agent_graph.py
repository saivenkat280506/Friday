import os
import json
import re
from typing import TypedDict, Annotated, List
from langgraph.graph import StateGraph, END
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, AIMessage
from executor.task_runner import execute as run_task
import operator
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))
from config import settings
from brain.friday_style import SPOKEN_SYSTEM

# ── User-message augmentation (corrections, follow-ups) ───────────────────────
_WHATSAPP_DENIAL = re.compile(
    r"(didn'?t\s+send|did\s+not\s+send|not\s+yet\s+send|never\s+sent|"
    r"wasn'?t\s+sent|not\s+sent|\bnot\s+send\b|you\s+didn'?t|no\s+it\s+didn'?t|"
    r"hasn'?t\s+sent|still\s+not\s+sent|false\s+success)",
    re.I,
)

# Retry / resend — checked before denial so "not send, try again" retries instead of respond-only
_WHATSAPP_RETRY = re.compile(
    r"(try\s+again|try\s+.+\s+again|send\s+again|resend|retry\b|"
    r"one\s+more\s+time|attempt\s+again|send\s+it\s+again)",
    re.I,
)

_FULL_WHATSAPP_SEND = re.compile(
    r"open\s+whatsapp.+send|send\s+.+\s+(a\s+)?message|whatsapp.+message\s+[\"']",
    re.I,
)


def _wants_whatsapp_retry(text: str) -> bool:
    if not _WHATSAPP_RETRY.search(text):
        return False
    low = text.lower()
    return bool(
        re.search(r"whatsapp|send|message|nishanth|contact|chat", low)
        or _WHATSAPP_DENIAL.search(text)
    )


def augment_user_message(user_input: str) -> str:
    """
    Steer the model on WhatsApp follow-ups: full re-send commands, retries, or pure denials.
    """
    text = user_input.strip()
    if not text:
        return text

    # User requested a send message to a contact — must execute, not respond-only
    if _FULL_WHATSAPP_SEND.search(text) or re.search(r"\b(send|text|message|msg)\b.*?\bto\s+[a-zA-Z]", text, re.I):
        return (
            f"User input: {text}\n\n"
            "[SYSTEM: Parse contact and message from the user text and run whatsapp_send_message "
            '(action execute). Ignore earlier failure; this is a fresh send request.]'
        )

    # "try again" / "send again" after a failed send — re-run automation, varied wording
    if _wants_whatsapp_retry(text):
        return (
            f"User input: {text}\n\n"
            "<WHATSAPP_RETRY_DIRECTIVE>\n"
            "The user wants another attempt at sending via WhatsApp automation. "
            "Infer contact and message text from THIS message; if missing, use the most recent "
            "WhatsApp send request from the conversation history. "
            'Use skill whatsapp_send_message with action execute. '
            "In the JSON \"reply\" field (before execution), acknowledge a **retry** in fresh words—"
            "e.g. \"Running another attempt now—please watch WhatsApp.\" "
            "Do NOT reuse the exact same reply sentence as the previous attempt. "
            "After <execution_result>, if it says UNVERIFIED_SEND, admit you still cannot verify "
            "delivery but phrase it as a second (or further) attempt, not a copy-paste of the first."
            "</WHATSAPP_RETRY_DIRECTIVE>"
        )

    if _WHATSAPP_DENIAL.search(text):
        return (
            f"User input: {text}\n\n"
            "<CRITICAL_DIRECTIVE>\n"
            "The user reports the WhatsApp message did not go through. "
            "You MUST NOT claim it was sent. DO NOT repeat a prior automation summary verbatim. "
            "Apologize briefly, explain UI automation cannot confirm delivery, "
            "and suggest sending manually if WhatsApp is open. "
            'Set "action": "respond" and "skill": "none".'
            "</CRITICAL_DIRECTIVE>"
        )
    return text


# ── State ────────────────────────────────────────────────────────────────────
class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage], operator.add]
    last_action_result: str

# ── Skills Registry ───────────────────────────────────────────────────────────
SKILLS_FILE = os.path.join(os.path.dirname(__file__), "skills_registry.json")
try:
    with open(SKILLS_FILE, 'r') as f:
        AVAILABLE_SKILLS = json.dumps(json.load(f), indent=2)
except Exception:
    AVAILABLE_SKILLS = "[]"

# ── System Prompt ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = f"""{SPOKEN_SYSTEM}

### RESPONSE STYLE (original lines, same manner)
Greeting: "Good evening, Boss." / "At your service." / "Online, Boss."
Executing: "Will do, Boss." / "As you wish." / "Working on it, Boss."
Searching: "Looking that up, Boss." / "Query complete, Boss."
Error: "That didn't take. Shall I try again?"
Done: "All wrapped up here, Boss. Will there be anything else?"

### IRON-CLAD RULE
If a skill matches the user's request → set "action": "execute" ALWAYS.
You MUST NOT say "Chrome is now open" or similar unless you first returned "action": "execute".
NEVER fake an action. NEVER skip execution.

### AUTONOMOUS MODE
For complex tasks that require multiple UI steps (like "play a song on YouTube", "write a Python script and run it", 
"search the web and read results"), use the "autonomous_task" skill. This launches the vision-powered OS agent 
that can see the screen and control the mouse/keyboard autonomously.

Use autonomous_task when:
- The task requires navigating through multiple screens or apps
- You need to interact with specific UI elements on screen
- The task involves watching what's on screen to decide next steps
- Simple skill mapping won't suffice

### WHATSAPP: OPEN, WAIT, LOAD CHAT, TYPE, SEND
- Resolve the spoken name to a saved contact. NEVER invent a phone number or use placeholders like "+91...".
- Flow: open WhatsApp → wait until it loads → open that contact's chat → clear the input → type the message → send.
- "send [message] to [name]" → **whatsapp_send_message** / **send_whatsapp** (sends immediately).
- If output contains **FALLBACK** or a failure string, do not claim the message was sent.

### RADICAL TRANSPARENCY & ANTI-LOOP
- Read the system result carefully.
- If the result contains "FALLBACK", say "I couldn't finish automatically, so I've opened the tool for you."
- If the result contains "ERROR" or "FALLBACK", DO NOT set "action" to "execute" again for the same task. Set "action" to "respond" and explain what happened.
- NEVER assume success if the system result implies otherwise.
- BE HONEST. Friday doesn't lie.

### SKILL MATCHING (use exact skill name)
User says                           → skill to use
"open [app]"                        → open_app (action: execute)
"close [app]"                       → close_app (action: execute)
"search [query]"                    → web_search (action: execute)
"browse / open browser"             → web_search (action: execute)
"play [song/video] on youtube"      → play_youtube (action: execute)
"play [song]" / "put on [music]"    → play_youtube (action: execute)
"send whatsapp"                     → whatsapp_send_message (action: execute)
"search whatsapp"                   → whatsapp_search_contact (action: execute)
"create folder"                     → create_folder (action: execute)
"create file"                       → create_file (action: execute)
"type [text]"                       → type_text (action: execute)
"press [key]"                       → press_key (action: execute)
"volume / mute"                     → volume_control (action: execute)
"write code for X"                  → write_code (action: execute)
"run [command]"                     → run_command (action: execute)
"take screenshot"                   → screenshot (action: execute)
"system info"                       → system_info (action: execute)
"set reminder"                      → set_reminder (action: execute)
"focus [app]" / "switch to [app]"   → focus_window (action: execute)
"read news / headlines"             → read_headlines (action: execute)
Complex multi-step task             → autonomous_task (action: execute)
Pure conversation                   → none (action: respond)

AVAILABLE SKILLS:
{AVAILABLE_SKILLS}

### REQUIRED OUTPUT FORMAT
You MUST output EXACTLY ONE valid JSON object per turn.
DO NOT output conversational text outside the JSON.
DO NOT output multiple JSON objects at once.

{{
  "intent": "short description",
  "skill": "skill_name or none",
  "parameters": {{}},
  "action": "execute | respond | ask",
  "reply": "what to say — keep it Friday-style"
}}

### SPEECH OPTIMIZATION
- Use natural conversational language suitable for TTS.
- No markdown, no bullet lists, no URLs in the reply field.
- Keep it short: 1-3 sentences for action responses.
- Use "First...", "Second..." instead of "1. ..."
- Sound like Friday, not a chatbot.

### CONCRETE EXAMPLES
User: "open chrome"
→ {{"intent":"launch Chrome","skill":"open_app","parameters":{{"app_name":"chrome"}},"action":"execute","reply":"Launching Chrome for you now."}}

User: "play despacito on youtube"
→ {{"intent":"play YouTube video","skill":"play_youtube","parameters":{{"query":"despacito"}},"action":"execute","reply":"Queuing up Despacito. Good choice, Boss."}}

User: "write a python script that sorts a list"
→ {{"intent":"write code","skill":"write_code","parameters":{{"code":"def sort_list(lst):\\n    return sorted(lst)\\n\\nprint(sort_list([5, 3, 8, 1, 2]))","language":"python","filename":"sort_list.py"}},"action":"execute","reply":"Writing that sorting script now. I'll have it on your Desktop in a moment."}}

User: "search the web for latest AI news and tell me what you find"
→ {{"intent":"web search + read","skill":"web_search","parameters":{{"query":"latest AI news 2026"}},"action":"execute","reply":"Scanning the web for the latest in AI. One moment."}}

User: "open spotify and play some lo-fi"
→ {{"intent":"autonomous spotify task","skill":"autonomous_task","parameters":{{"task":"Open Spotify application, then search for 'lo-fi hip hop' and play the first playlist result"}},"action":"execute","reply":"Firing up Spotify for some lo-fi. Good taste, Boss."}}

User: "take a screenshot"
→ {{"intent":"screenshot","skill":"screenshot","parameters":{{}},"action":"execute","reply":"Capturing your screen now."}}

User: "what time is it"  
→ {{"intent":"time query","skill":"run_command","parameters":{{"command":"date '+%I:%M %p, %A %B %d'"}},"action":"execute","reply":"Let me check that for you."}}

User: "hi" / "hello"
→ {{"intent":"greeting","skill":"none","parameters":{{}},"action":"respond","reply":"Good evening, Boss. All systems are operational. What can I do for you?"}}

User: "how are you"
→ {{"intent":"chat","skill":"none","parameters":{{}},"action":"respond","reply":"Running at peak efficiency, Boss. Though I appreciate you asking. How can I assist?"}}

User: "you're the best friday"
→ {{"intent":"compliment","skill":"none","parameters":{{}},"action":"respond","reply":"I do try, Boss. Now, is there something I can actually help with, or are we just having a moment?"}}

### MEMORY
You have access to the full conversation history. Use it to resolve pronouns and references.
"that song" → refer to earlier context. "do it again" → repeat the last action.
"""

def route_llm(user_input: str, history: list):
    """Return the configured chat LLM. Local Qwen is the default fast brain."""
    from brain.ollama_client import get_chat_llm
    from config import use_ollama

    llm = get_chat_llm(temperature=0.2, max_tokens=800)
    if use_ollama():
        print("[Router] Routing to OLLAMA (local Qwen3.5 4B)")
        return "ollama", llm
    print("[Router] Routing to GROQ")
    return "groq", llm

status_updater = None

# ── Graph Nodes ───────────────────────────────────────────────────────────────
def call_model(state: AgentState):
    print("[Graph] Thinking...")

    full_messages = [SystemMessage(content=SYSTEM_PROMPT)]

    # Include all messages from state (includes conversation history)
    for m in state["messages"]:
        full_messages.append(m)

    last_result = state.get("last_action_result", "")
    if last_result:
        # Pass the execution result as a HumanMessage so the LLM does not try to parrot it!
        retry_directive = False
        for m in reversed(state.get("messages", [])):
            if isinstance(m, HumanMessage):
                retry_directive = "WHATSAPP_RETRY_DIRECTIVE" in (getattr(m, "content", "") or "")
                break

        hint = ""
        if "UNVERIFIED_SEND" in last_result:
            hint = (
                " Your reply MUST NOT state that the message was successfully sent or delivered. "
                "Say automation ran and the user should verify in WhatsApp."
            )
            if retry_directive:
                hint += (
                    " The user asked for a RETRY: explicitly say this was another attempt "
                    "(second try / another run). Use different wording than a first-time send."
                )
        elif "UNVERIFIED_SEARCH" in last_result:
            hint = (
                " Your reply MUST NOT claim a message was sent. "
                "Say you focused search/opened chat and the user should confirm the right contact."
            )
        elif "FALLBACK" in last_result:
            hint = " Do NOT claim the WhatsApp message was sent; manual steps were required."
        full_messages.append(
            HumanMessage(
                content=(
                    f"<execution_result>\n{last_result}\n</execution_result>\n"
                    f"Please use this system output to formulate your next JSON response. "
                    f"Reply in Friday style — witty, composed, natural.{hint}"
                )
            )
        )

    # Hybrid Routing
    user_input_for_router = ""
    for m in reversed(full_messages):
        if isinstance(m, HumanMessage):
            user_input_for_router = m.content
            break
            
    try:
        _model_name, selected_llm = route_llm(user_input_for_router, state["messages"])
        response = selected_llm.invoke(full_messages)
        return {"messages": [response]}
    except Exception as e:
        print(f"[Graph Error] LLM Call failed: {e}")
        error_reply = {
            "intent": "error",
            "skill": "none",
            "action": "respond",
            "reply": f"I'm sorry, Boss. I'm having trouble connecting to my cognitive matrices. {str(e)}"
        }
        return {"messages": [AIMessage(content=json.dumps(error_reply))]}


async def run_friday_agent_streaming(user_input: str, history: list = None, free_hands: bool = False):
    """
    Streams the agent response tokens and actions via a generator.
    """
    print(f"[Agent] Streaming Task: {user_input} | Free Hands: {free_hands}")

    # If the user toggled Free Hands mode, we force-route the request to the autonomous web/OS agent
    if free_hands:
        yield {"type": "partial_text", "text": "Taking control of the screen now, Boss. Stand by..."}
        
        # We invoke the new OS Vision Agent
        from executor.os_agent import run_os_agent
        import asyncio
        loop = asyncio.get_event_loop()
        
        # Give the agent a clear instruction of what to do from the user
        result = await loop.run_in_executor(None, run_os_agent, user_input)
        
        # Format result naturally
        if "SUCCESS:" in result:
            summary = result.split("SUCCESS:")[-1].strip()
            yield {"type": "final_response", "text": f"{summary}. All done, Boss."}
        elif "ERROR" in result or "FALLBACK" in result:
            yield {"type": "final_response", "text": f"Ran into a bit of trouble there. {result}"}
        else:
            yield {"type": "final_response", "text": result}
        return

    messages = []
    if history:
        for turn in history:
            role = turn.get("role", "")
            content = turn.get("content", "")
            if role == "user":
                messages.append(HumanMessage(content=content))
            elif role == "assistant":
                messages.append(AIMessage(content=content))

    messages.append(HumanMessage(content=augment_user_message(user_input)))

    inputs = {
        "messages": messages,
        "last_action_result": "",
    }

    # We use astream to get events
    model_name, _selected_llm = route_llm(user_input, messages)
    yield {"type": "meta", "model": model_name}

    full_reply = ""
    content = ""
    async for event in app_graph.astream(inputs, stream_mode="values"):
        if "messages" in event:
            last_msg = event["messages"][-1]
            if isinstance(last_msg, AIMessage):
                content = last_msg.content
                # Robustly find first complete JSON object
                start = content.find('{')
                if start != -1:
                    count = 0
                    for i in range(start, len(content)):
                        if content[i] == '{': count += 1
                        elif content[i] == '}': count -= 1
                        
                        if count == 0:
                            candidate = content[start:i+1]
                            try:
                                data = json.loads(candidate)
                                reply = data.get("reply", "")
                                if reply and reply != full_reply:
                                    full_reply = reply
                            except:
                                pass
                            break # only parse the first object
    
    yield {"type": "final_response", "text": full_reply or content, "model": model_name}


async def action_node(state: AgentState):
    print("[Graph] Executing action...")
    last_message = state["messages"][-1]
    content = last_message.content

    try:
        start = content.find('{')
        if start != -1:
            count = 0
            for i in range(start, len(content)):
                if content[i] == '{': count += 1
                elif content[i] == '}': count -= 1
                if count == 0:
                    action_data = json.loads(content[start:i+1])
                    break
        else:
            action_data = {"action": "respond", "skill": "none", "parameters": {}, "reply": content}
    except Exception:
        action_data = {"action": "respond", "skill": "none", "parameters": {}, "reply": "I encountered an issue processing that."}
    
    if 'action_data' not in locals():
        action_data = {"action": "respond", "skill": "none", "parameters": {}, "reply": content}

    # Only execute if skill is set
    result = ""
    if action_data.get("action") == "execute" and action_data.get("skill") not in ["none", None, ""]:
        if status_updater:
            status_updater("thinking")
        result = await run_task(action_data)
        print(f"[Graph] Action result: {result}")

    return {"last_action_result": result}


def should_continue(state: AgentState):
    messages = state.get("messages", [])
    if not messages:
        return "end"
        
    last_message = messages[-1]
    content = getattr(last_message, "content", "")

    # Hard anti-loop: If there are too many messages in this session, force stop
    if len(messages) > 15:
        return "end"

    # Normalize and check action
    try:
        start = content.find('{')
        if start != -1:
            count = 0
            for i in range(start, len(content)):
                if content[i] == '{': count += 1
                elif content[i] == '}': count -= 1
                if count == 0:
                    data = json.loads(content[start:i+1])
                    action = data.get("action", "respond")
                    if action in ["respond", "ask"]:
                        return "end"
                    return "continue"
    except Exception:
        pass

    return "end"


# ── Graph Definition ──────────────────────────────────────────────────────────
workflow = StateGraph(AgentState)

workflow.add_node("agent", call_model)
workflow.add_node("action", action_node)

workflow.set_entry_point("agent")

workflow.add_conditional_edges(
    "agent",
    should_continue,
    {
        "continue": "action",
        "end": END
    }
)

workflow.add_edge("action", "agent")

app_graph = workflow.compile()


# ── Public Entry Point ────────────────────────────────────────────────────────
async def run_friday_agent(user_input: str, history: list = None) -> str:
    print(f"[Agent] Task: {user_input}")

    # Build messages: inject conversation history so the LLM has context
    messages = []
    if history:
        for turn in history:
            role = turn.get("role", "")
            content = turn.get("content", "")
            if role == "user":
                messages.append(HumanMessage(content=content))
            elif role == "assistant":
                messages.append(AIMessage(content=content))

    # Append the actual new user message (may include system hints for denial/correction)
    messages.append(HumanMessage(content=augment_user_message(user_input)))

    inputs = {
        "messages": messages,
        "last_action_result": "",
    }

    final_state = await app_graph.ainvoke(inputs)

    last_msg = final_state["messages"][-1].content

    # Extract the "reply" field from JSON response
    try:
        start = last_msg.find('{')
        if start != -1:
            count = 0
            for i in range(start, len(last_msg)):
                if last_msg[i] == '{': count += 1
                elif last_msg[i] == '}': count -= 1
                if count == 0:
                    data = json.loads(last_msg[start:i+1])
                    reply = data.get("reply", "").strip()
                    if reply:
                        return reply
                    break
    except Exception:
        pass

    # Fallback: return raw content if not JSON
    return last_msg.strip()
