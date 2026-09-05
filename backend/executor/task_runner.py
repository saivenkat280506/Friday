"""
task_runner.py — Expanded Task Executor
=======================================
Handles all skill executions dispatched from the agent graph.
Now supports: YouTube, code writing, system info, screenshots, 
app management, web search, WhatsApp, file ops, and more.
"""

import os
import shutil
import subprocess
import time
import threading
import webbrowser
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import socket
import re
import json
import asyncio
from executor.open_app import get_app_path, open_app, close_app
from executor.sys_platform import (
    IS_MAC,
    MOD_KEY,
    current_user,
    desktop_dir,
    focus_application,
    notify,
    system_power,
)

# --- 3rd Party Dependencies ---
try:
    import pyautogui
    from PIL import Image
    pyautogui.FAILSAFE = False
except ImportError:
    pyautogui = None

try:
    import pytesseract
except ImportError:
    pytesseract = None

keyboard = None
Application = None

# --- Configuration & Flags ---
OLLAMA_ENABLED = False

def is_ollama_running() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 11434), timeout=1):
            return True
    except OSError:
        return False

# Initial Check
if is_ollama_running():
    print("[FRIDAY] Ollama detected on port 11434. Local fallback enabled.")
    OLLAMA_ENABLED = True
else:
    print("[FRIDAY] Ollama not detected. Using cloud-only (Groq) brain.")

# Track context
_last_search_query: str = ""

# ══════════════════════════════════════════════════════════════════════════════
# 1. VISION VERIFICATION (Self-Correction Logic)
# ══════════════════════════════════════════════════════════════════════════════

def verify_via_screenshot(target_text: str) -> bool:
    """
    Second-order fallback verification. If UI Tree scanning misses the bubble,
    we take a raw screenshot of the chat area and use OCR to find the text.
    """
    if not pyautogui or not pytesseract:
        return False
    
    try:
        screenshot = pyautogui.screenshot(region=(400, 200, 1500, 800))
        ocr_text = pytesseract.image_to_string(screenshot)
        clean_target = target_text.lower().strip()
        clean_ocr = ocr_text.lower().strip()
        return clean_target in clean_ocr
    except Exception as e:
        print(f"[Vision] Verification Error: {e}")
        return False

# ══════════════════════════════════════════════════════════════════════════════
# 2. CORE AUTOMATION LOGIC
# ══════════════════════════════════════════════════════════════════════════════

def type_text(text: str) -> str:
    if not text:
        return "No text provided to type."
    time.sleep(0.5)
    if pyautogui:
        try:
            import pyperclip
            pyperclip.copy(text)
            pyautogui.hotkey(MOD_KEY, "v")
            return f"SUCCESS: Typed '{text}'"
        except Exception:
            pyautogui.write(text, interval=0.02)
            return f"SUCCESS: Typed '{text}'"
    if keyboard:
        keyboard.write(text, delay=0.02)
        return f"SUCCESS: Typed '{text}'"
    return "ERROR: no typing backend is available."

def press_key(key: str) -> str:
    """Press a keyboard key or hotkey combo."""
    raw = (key or "").strip()
    if not raw:
        return "ERROR: No key provided."
    try:
        parts = [p.strip().lower() for p in raw.split("+") if p.strip()]
        mapped = []
        for part in parts:
            if IS_MAC and part in ("ctrl", "control"):
                part = "command"
            if IS_MAC and part == "alt":
                part = "option"
            if IS_MAC and part == "win":
                part = "command"
            mapped.append(part)
        if pyautogui:
            if len(mapped) == 1:
                pyautogui.press(mapped[0])
            else:
                pyautogui.hotkey(*mapped)
            return f"SUCCESS: Pressed '{'+'.join(mapped)}'"
        if keyboard:
            keyboard.send("+".join(mapped))
            return f"SUCCESS: Pressed '{'+'.join(mapped)}'"
        return "ERROR: no keyboard backend is available."
    except Exception as e:
        return f"ERROR: Failed to press key: {e}"

async def search_web(query: str, resolved_browser_path: str = "") -> str:
    from executor.automation import search_and_summarize_in_notepad
    success, result = await asyncio.to_thread(search_and_summarize_in_notepad, query)
    return result

def play_youtube(query: str) -> str:
    """Play a YouTube result via Puppeteer + Google Chrome."""
    if not query:
        from executor.browser_puppeteer import browser_navigate
        ok, msg = browser_navigate({"url": "https://www.youtube.com"})
        return f"SUCCESS: {msg}" if ok else f"ERROR: {msg}"
    from executor.browser_puppeteer import youtube_play_puppeteer
    ok, msg = youtube_play_puppeteer(query, service="youtube")
    return f"SUCCESS: {msg}" if ok else f"ERROR: {msg}"

def read_news_headlines(query: str) -> str:
    try:
        query = query.strip() or "top stories"
        encoded = urllib.parse.quote(query)
        rss_url = f"https://news.google.com/rss/search?q={encoded}&hl=en-IN&gl=IN&ceid=IN:en"
        req = urllib.request.Request(rss_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            xml_data = resp.read()
        root = ET.fromstring(xml_data)
        items = root.findall(".//item")
        headlines = [item.find("title").text.strip() for item in items[:3] if item.find("title") is not None]
        cleaned_headlines = []
        for h in headlines:
            cleaned_h = re.sub(r' - [^-]+$', '', h)
            cleaned_headlines.append(cleaned_h)
        if cleaned_headlines: return f"FOUND headlines for '{query}':\n" + "\n".join(f"- {h}" for h in cleaned_headlines)
        return f"EMPTY: No news found for '{query}'."
    except Exception as e:
        return f"ERROR: Could not fetch news: {e}"

async def send_whatsapp(contact: str, message: str, number: str = "") -> str:
    if not contact and not number:
        return "ERROR: No contact or phone number specified."
    from executor.automation import prepare_whatsapp_message
    from brain.memory import save_memory
    import asyncio
    success, msg = await asyncio.to_thread(prepare_whatsapp_message, contact, message, number)
    if success:
        save_memory("last_contact", contact or number)
        save_memory("pending_whatsapp", None)
    return msg

def write_code(code: str, filename: str = "", language: str = "python") -> str:
    """Write code to a file and optionally open it in VS Code."""
    if not code:
        return "ERROR: No code provided to write."
    
    # Determine file extension
    ext_map = {
        "python": ".py", "javascript": ".js", "typescript": ".ts",
        "html": ".html", "css": ".css", "java": ".java",
        "cpp": ".cpp", "c": ".c", "rust": ".rs", "go": ".go",
        "dart": ".dart", "ruby": ".rb", "php": ".php",
    }
    ext = ext_map.get(language.lower(), ".txt")
    
    if not filename:
        filename = f"friday_code{ext}"
    elif not os.path.splitext(filename)[1]:
        filename += ext
    
    desktop = desktop_dir()
    filepath = os.path.join(desktop, filename)
    
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(code)
        
        # Try to open in VS Code
        try:
            subprocess.Popen(["code", filepath], shell=False)
        except Exception:
            if IS_MAC:
                subprocess.Popen(["open", "-a", "TextEdit", filepath], shell=False)
            else:
                subprocess.Popen(f'notepad "{filepath}"', shell=True)
        
        return f"SUCCESS: Code written to '{filename}' on Desktop and opened in editor."
    except Exception as e:
        return f"ERROR: Failed to write code: {e}"

def run_terminal_command(command: str) -> str:
    """Execute a terminal/PowerShell command and return output."""
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=30
        )
        output = result.stdout.strip() or result.stderr.strip() or "Command completed successfully"
        return f"SUCCESS: {output[:1000]}"
    except subprocess.TimeoutExpired:
        return "ERROR: Command timed out after 30 seconds"
    except Exception as e:
        return f"ERROR: {e}"

def get_system_info() -> str:
    """Get system information."""
    import platform
    try:
        info = {
            "os": platform.platform(),
            "processor": platform.processor(),
            "machine": platform.machine(),
            "user": current_user(),
            "hostname": platform.node(),
        }
        return f"SUCCESS: {json.dumps(info)}"
    except Exception as e:
        return f"ERROR: {e}"

def take_screenshot() -> str:
    """Take a screenshot and save to desktop."""
    try:
        from vision.capture import capture_screen_base64
        import base64
        b64 = capture_screen_base64(draw_boxes=False)
        # Save to desktop
        screenshot_path = os.path.join(desktop_dir(), "friday_screenshot.png")
        img_data = base64.b64decode(b64)
        with open(screenshot_path, 'wb') as f:
            f.write(img_data)
        return f"SUCCESS: Screenshot saved to Desktop as 'friday_screenshot.png'"
    except Exception as e:
        return f"ERROR: {e}"

def focus_window(app_name: str) -> str:
    """Bring an application window to the foreground."""
    try:
        ok, msg = focus_application(app_name)
        return f"SUCCESS: {msg}" if ok else f"ERROR: {msg}"
    except Exception as e:
        return f"ERROR: Could not focus '{app_name}': {e}"

def click_ui_element(app_name: str, element_name: str) -> str:
    """Click a UI element inside an app by name."""
    try:
        ok, msg = focus_application(app_name)
        if not ok:
            return f"ERROR: {msg}"
        time.sleep(0.4)
        from executor.os_agent import run_os_agent
        return run_os_agent(
            f"In {app_name}, click the UI element named '{element_name}'.",
            max_steps=4,
            use_vision=True,
        )
    except Exception as e:
        return f"ERROR: {e}"

def set_reminder(minutes: int, message: str) -> str:
    """Set a reminder that triggers after N minutes."""
    def _reminder_thread():
        time.sleep(minutes * 60)
        try:
            from tts.pocket_tts import speak
            speak(f"Reminder: {message}")
            notify("FRIDAY Reminder", message)
        except Exception as e:
            print(f"[Reminder] Error: {e}")
    
    threading.Thread(target=_reminder_thread, daemon=True).start()
    return f"SUCCESS: Reminder set for {minutes} minutes from now: '{message}'"


# ══════════════════════════════════════════════════════════════════════════════
# 3. AUTONOMOUS WEB AGENT (Browser-Use)
# ══════════════════════════════════════════════════════════════════════════════

async def run_autonomous_web_task(task_description: str) -> str:
    """
    Deep-vision web agent using browser-use.
    """
    try:
        from brain.ollama_client import get_chat_llm
        from browser_use import Agent
        
        llm = get_chat_llm(temperature=0.1, max_tokens=400)
        agent = Agent(task=task_description, llm=llm)
        
        history = await agent.run()
        
        final_result = "Task completed successfully."
        if hasattr(history, 'final_result') and history.final_result():
            final_result = history.final_result()
        elif hasattr(history, 'history') and len(history.history) > 0:
            last_event = history.history[-1]
            if hasattr(last_event, 'text'): final_result = last_event.text
            
        return f"SUCCESS: Autonomous Web Agent reports: {final_result}"
    except Exception as e:
        return f"WEB_AGENT_ERROR: {str(e)}"


# ══════════════════════════════════════════════════════════════════════════════
# 4. HELPER: App Resolver
# ══════════════════════════════════════════════════════════════════════════════

def _resolve_app_path(app_name: str) -> str:
    # 1. Check our new registry first
    discovered = get_app_path(app_name)
    if discovered:
        return discovered
    
    # 2. Existing hardcoded logic and system fallbacks
    orig_name = app_name.lower().strip()
    app_name = orig_name.replace(".exe", "")
    aliases = {
        "browser": "chrome", "google": "chrome", "calc": "ms-calculator:", 
        "calculator": "ms-calculator:", "word": "winword", "powerpoint": "powerpnt",
        "terminal": "wt", "cmd": "cmd", "explorer": "explorer", "vscode": "code",
        "vs code": "code", "visual studio code": "code",
        "arc browser": "arc", "microsoft edge": "msedge", "edge": "msedge",
        "settings": "ms-settings:", "camera": "microsoft.windows.camera:",
        "maps": "bingmaps:", "calendar": "outlookcal:", "mail": "outlookmail:",
        "spotify": "spotify", "discord": "discord", "telegram": "telegram",
        "notepad": "notepad", "paint": "mspaint", "snipping tool": "snippingtool",
        "task manager": "taskmgr", "control panel": "control",
        "file explorer": "explorer", "files": "explorer",
    }
    target = aliases.get(app_name, app_name)
    if target.endswith(":"): return target
    
    # Check if target is already a full path
    if os.path.exists(target):
        return target
        
    # Check shutil.which
    which_path = shutil.which(target) or shutil.which(f"{target}.exe")
    return which_path if which_path else target


# ══════════════════════════════════════════════════════════════════════════════
# 5. MAIN EXECUTOR ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

async def execute(groq_output: dict) -> str:
    action_type = groq_output.get("action", "")
    if action_type in ["respond", "ask"]: return ""
    skill = groq_output.get("skill", "")
    params = groq_output.get("parameters", {})
    if not skill or skill == "none": return "No action."

    try:
        # ── App Control ──
        if skill == "open_app":
            app = (params.get("app_name") or params.get("app") or "").strip()
            ok, msg = open_app(app)
            return msg if ok else f"ERROR: {msg}"

        elif skill == "close_app":
            app = (params.get("app_name") or params.get("app") or "").strip()
            ok, msg = close_app(app)
            return msg if ok else f"ERROR: {msg}"

        elif skill == "focus_window":
            app = (params.get("app_name") or params.get("app") or "").strip()
            return focus_window(app)

        elif skill == "click_ui_element":
            app = (params.get("app_name") or "").strip()
            element = (params.get("element_name") or "").strip()
            return click_ui_element(app, element)

        # ── Web & News ──
        elif skill in ("web_search", "web_task", "research_topic"):
            global _last_search_query
            query = params.get("query") or params.get("topic") or ""
            _last_search_query = query if query else _last_search_query
            
            # Note: run_autonomous_web_task should also be awaited if it's async
            if "research" in skill or params.get("autonomous", False):
                from executor.os_agent import run_os_agent
                return await asyncio.to_thread(run_os_agent, f"Search the web for '{query}' and summarize the key findings.")

            browser = params.get("browser", "").lower().strip()
            resolved = _resolve_app_path(browser) if browser else ""
            return await search_web(query, resolved)

        elif skill in ("read_headlines", "fetch_news", "read_news"):
            query = params.get("query") or params.get("topic") or _last_search_query or "top news"
            return read_news_headlines(query)

        # ── YouTube ──
        elif skill in ("play_youtube", "youtube_search", "youtube"):
            query = params.get("query") or params.get("video") or params.get("song") or ""
            return play_youtube(query)

        # ── Messaging (always search by phone number; draft then confirm) ──
        elif skill in ("send_whatsapp", "whatsapp_send_message"):
            contact = params.get("contact") or params.get("name") or ""
            number = params.get("number") or params.get("phone") or ""
            msg = params.get("message") or params.get("text") or ""
            if msg:
                return await send_whatsapp(contact, msg, number=number)
            return "ERROR: Message required."

        elif skill == "whatsapp_search_contact":
            contact = params.get("contact") or params.get("name") or ""
            number = params.get("number") or params.get("phone") or ""
            return await send_whatsapp(contact, "", number=number)

        elif skill == "confirm_whatsapp_send":
            from executor.automation import confirm_send_whatsapp_message
            from brain.memory import get_memory, save_memory
            import asyncio
            pending = get_memory("pending_whatsapp") or {}
            if not pending.get("awaiting_confirm"):
                return "There is no WhatsApp message waiting to be sent, Boss."
            ok, msg = await asyncio.to_thread(confirm_send_whatsapp_message)
            if ok:
                save_memory("pending_whatsapp", None)
            return msg

        elif skill == "cancel_whatsapp_send":
            from executor.automation import cancel_whatsapp_draft
            from brain.memory import save_memory
            import asyncio
            ok, msg = await asyncio.to_thread(cancel_whatsapp_draft)
            save_memory("pending_whatsapp", None)
            return msg

        # ── System ──
        elif skill == "volume_control":
            from executor.volume_control import volume_control
            ok, msg = volume_control(params or {"action": "up"})
            return msg if ok else f"ERROR: {msg}"

        elif skill == "system_info":
            return get_system_info()

        elif skill == "screenshot":
            return take_screenshot()

        elif skill == "shutdown":
            ok, msg = system_power(params.get("mode", "shutdown"))
            return msg if ok else f"ERROR: {msg}"

        # ── Typing & Input ──
        elif skill == "type_text":
            return type_text(params.get("text", ""))

        elif skill == "press_key":
            key = params.get("key", "")
            return press_key(key)

        # ── Code Writing ──
        elif skill in ("write_code", "create_code"):
            code = params.get("code", "")
            filename = params.get("filename", params.get("file_name", ""))
            language = params.get("language", "python")
            return write_code(code, filename, language)

        elif skill in ("run_code", "run_terminal", "run_command"):
            command = params.get("command", params.get("query", ""))
            return run_terminal_command(command)

        # ── File System ──
        elif skill == "create_folder":
            name = params.get("folder_name") or params.get("name") or "New Folder"
            path = params.get("path") or desktop_dir()
            os.makedirs(os.path.join(path, name), exist_ok=True)
            return f"SUCCESS: Created folder '{name}'."

        elif skill == "create_file":
            name = params.get("file_name") or params.get("name") or "new_file.txt"
            path = params.get("path") or desktop_dir()
            content = params.get("content", "")
            filepath = os.path.join(path, name)
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            return f"SUCCESS: Created file '{name}'."

        # ── Reminders ──
        elif skill == "set_reminder":
            minutes = int(params.get("time", params.get("minutes", 5)))
            message = params.get("message", "Time's up!")
            return set_reminder(minutes, message)

        # ── Autonomous OS Agent (catch-all for complex tasks) ──
        elif skill == "autonomous_task":
            task = params.get("task", params.get("description", ""))
            from executor.os_agent import run_os_agent
            return run_os_agent(task)

        return f"Skill '{skill}' not implemented."

    except Exception as e:
        return f"Error: {e}"
