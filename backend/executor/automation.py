"""
automation.py — Lightweight Automation Suite
===========================================
Provides simple system and browser automation without heavy dependencies.
WhatsApp send logic lives in executor.whatsapp_handler; this module keeps
shared launch/verification helpers and iterative search logging via Notepad.
"""

import subprocess
import os
import webbrowser
import urllib.parse
import time
import pyautogui
from pywinauto import Application, keyboard
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))
from config import settings
from executor.error_handler import retry_task, retry_async_task

pyautogui.PAUSE = 0.05

try:
    import cv2
    import numpy as np
    from mss import mss
    import threading
    import pyperclip
    HAS_RECORDING_DEPS = True
except ImportError:
    HAS_RECORDING_DEPS = False


def _safe_click(x: int, y: int) -> bool:
    """Use the shared visible cursor path for all coordinate clicks."""
    try:
        from executor.mouse_controller import click as safe_click
        success, msg = safe_click(x, y)
        if not success:
            print(f"[Automation] Click failed: {msg}")
        return success
    except Exception as e:
        print(f"[Automation] Click failed: {e}")
        return False

# ══════════════════════════════════════════════════════════════════════════════
# SCREEN RECORDER COMPONENT
# ══════════════════════════════════════════════════════════════════════════════

class ScreenRecorder:
    def __init__(self, filename, fps=8.0):
        self.filename = filename
        self.fps = fps
        self.recording = False
        self.thread = None
        
    def start(self):
        if not HAS_RECORDING_DEPS:
            print("[ScreenRecorder] Missing dependencies (cv2, numpy, mss). Cannot record.")
            return
        self.recording = True
        self.thread = threading.Thread(target=self._record_loop, name="ScreenRecorderLoop")
        self.thread.daemon = True
        self.thread.start()
        print(f"[ScreenRecorder] Started recording to {self.filename}")
        
    def stop(self):
        self.recording = False
        if self.thread:
            self.thread.join()
        print(f"[ScreenRecorder] Stopped recording and saved to {self.filename}")
        
    def _record_loop(self):
        try:
            with mss() as sct:
                monitor = sct.monitors[1]  # Primary monitor
                width = monitor["width"]
                height = monitor["height"]
                
                # Setup MP4 codec and VideoWriter
                fourcc = cv2.VideoWriter_fourcc(*'mp4v')
                out = cv2.VideoWriter(self.filename, fourcc, self.fps, (width, height))
                
                last_time = time.time()
                interval = 1.0 / self.fps
                
                while self.recording:
                    now = time.time()
                    elapsed = now - last_time
                    if elapsed < interval:
                        time.sleep(interval - elapsed)
                        
                    img = np.array(sct.grab(monitor))
                    frame = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
                    out.write(frame)
                    last_time = time.time()
                    
                out.release()
        except Exception as e:
            print(f"[ScreenRecorder] Error during capture: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# CORE AUTOMATION UTILITIES
# ══════════════════════════════════════════════════════════════════════════════

def _find_chrome_path():
    """Locate chrome.exe on the system."""
    import shutil
    chrome_path = shutil.which("chrome.exe") or shutil.which("chrome")
    if chrome_path:
        return chrome_path
    search_paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe"),
    ]
    for path in search_paths:
        if os.path.exists(path):
            return path
    return None


def open_browser(manual_path=None):
    """Opens Chrome browser or fallback."""
    chrome_path = manual_path or _find_chrome_path()
    if chrome_path:
        try:
            subprocess.Popen([chrome_path, "--profile-directory=Default"], shell=False)
            return True, "Successfully opened Chrome browser."
        except Exception as e:
            print(f"[open_browser] Failed to launch Chrome: {e}")
    
    try:
        import webbrowser
        webbrowser.open("about:blank")
        return True, "Chrome not found. Opened default browser as fallback."
    except Exception as e:
        return False, f"Failed to open any browser: {str(e)}"


def open_url_in_chrome(url):
    """Opens a URL in Chrome, falling back to webbrowser.open."""
    chrome_path = _find_chrome_path()
    if chrome_path:
        try:
            subprocess.Popen([chrome_path, "--profile-directory=Default", url], shell=False)
            return True
        except Exception as e:
            print(f"[open_url_in_chrome] Failed to launch Chrome: {e}")
            
    try:
        import webbrowser
        webbrowser.open(url)
        return True
    except Exception as e:
        print(f"[open_url_in_chrome] Failed to open default browser: {e}")
        return False


# Backward-compatible alias so existing callers keep working
open_url_in_arc = open_url_in_chrome

def _click_window_to_focus(window):
    """Click the center of a window to ensure it has active focus."""
    try:
        rect = window.rectangle()
        cx = (rect.left + rect.right) // 2
        cy = (rect.top + rect.bottom) // 2
        if not _safe_click(cx, cy):
            return False
        time.sleep(0.5)
        return True
    except Exception:
        return False


def _force_whatsapp_foreground(window):
    """Aggressively force WhatsApp window to foreground + click it."""
    try:
        import win32gui, win32con
        handle = window.handle
        # Restore if minimized
        fast = os.getenv("WHATSAPP_FAST", "").lower() in ("1", "true", "yes") or (
            os.getenv("WHATSAPP_DRY_RUN", "").lower() in ("1", "true", "yes")
        )
        delay = 0.1 if fast else 0.3
        win32gui.ShowWindow(handle, win32con.SW_RESTORE)
        time.sleep(delay)
        win32gui.SetForegroundWindow(handle)
        time.sleep(delay)
        win32gui.BringWindowToTop(handle)
        time.sleep(delay)
        # Also click the window title bar area to ensure focus
        rect = window.rectangle()
        if not _safe_click(rect.left + 100, rect.top + 10):
            return False
        time.sleep(0.5)
        return True
    except Exception as e:
        print(f"[WA] Force foreground failed: {e}")
        return False


_WHATSAPP_PROCESS_NAMES = frozenset({"whatsapp.exe", "whatsapp.root.exe"})


def _is_whatsapp_process_name(name: str) -> bool:
    return (name or "").lower() in _WHATSAPP_PROCESS_NAMES


def _whatsapp_process_pids() -> set[int]:
    """Return PIDs of running WhatsApp Desktop (classic or Microsoft Store build)."""
    pids: set[int] = set()
    try:
        import psutil

        for proc in psutil.process_iter(["pid", "name"]):
            if _is_whatsapp_process_name(proc.info.get("name") or ""):
                pids.add(proc.info["pid"])
        if pids:
            return pids
    except ImportError:
        pass
    except Exception as exc:
        print(f"[WA] psutil process scan failed: {exc}")

    try:
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        for line in result.stdout.splitlines():
            parts = [p.strip().strip('"') for p in line.split(",")]
            if len(parts) >= 2 and _is_whatsapp_process_name(parts[0]) and parts[1].isdigit():
                pids.add(int(parts[1]))
    except Exception as exc:
        print(f"[WA] tasklist process scan failed: {exc}")
    return pids


def is_whatsapp_running() -> bool:
    """True only when WhatsApp.exe is actually running (not IDE/browser tabs)."""
    return bool(_whatsapp_process_pids())


def _hwnd_belongs_to_whatsapp(hwnd: int) -> bool:
    """Return True if hwnd belongs to a WhatsApp.exe process."""
    if not hwnd:
        return False
    try:
        import win32process

        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return pid in _whatsapp_process_pids()
    except Exception:
        return False


def _get_whatsapp_window():
    """
    Find the WhatsApp Desktop window via pywinauto.

    Must belong to WhatsApp.exe — never match IDE/browser titles that contain
    the word 'WhatsApp' (e.g. Grok/Cursor chat tabs).
    """
    pids = _whatsapp_process_pids()
    if not pids:
        return None

    best = None
    best_area = 0
    for pid in pids:
        try:
            app = Application(backend="uia").connect(process=pid, timeout=2)
            for win in app.windows():
                try:
                    if not win.is_visible():
                        continue
                    rect = win.rectangle()
                    area = rect.width() * rect.height()
                    if area > best_area and rect.width() > 200 and rect.height() > 200:
                        best = win
                        best_area = area
                except Exception:
                    continue
            if best is None:
                try:
                    top = app.top_window()
                    if top.exists():
                        best = top
                except Exception:
                    pass
        except Exception:
            continue
    return best


def _find_whatsapp_executable() -> str | None:
    """Locate WhatsApp Desktop executable (classic installer or Store build)."""
    candidates = [
        os.path.join(
            os.path.expanduser("~"),
            "AppData",
            "Local",
            "WhatsApp",
            "WhatsApp.exe",
        ),
    ]
    try:
        import glob

        store_matches = glob.glob(
            r"C:\Program Files\WindowsApps\*\WhatsApp.Root.exe"
        )
        candidates.extend(store_matches)
    except Exception:
        pass

    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def open_whatsapp():
    """Opens WhatsApp Desktop, waits for the window, and ensures it's focused."""
    launched = False
    whatsapp_path = _find_whatsapp_executable()

    if whatsapp_path:
        try:
            subprocess.Popen([whatsapp_path], shell=False)
            launched = True
        except Exception as e:
            return False, f"Failed to launch WhatsApp Desktop: {str(e)}"
    else:
        try:
            subprocess.run("start whatsapp:", shell=True, check=True)
            launched = True
        except Exception as e:
            return False, f"Failed to launch WhatsApp: {str(e)}"

    if not launched:
        return False, "WhatsApp could not be launched."

    # Wait for window, then force focus
    fast = os.getenv("WHATSAPP_FAST", "").lower() in ("1", "true", "yes") or (
        os.getenv("WHATSAPP_DRY_RUN", "").lower() in ("1", "true", "yes")
    )
    deadline = time.time() + (12 if fast else 20)
    poll = 0.2 if fast else 0.5
    window = None
    while time.time() < deadline:
        if not is_whatsapp_running():
            time.sleep(poll)
            continue
        window = _get_whatsapp_window()
        if window is not None:
            break
        time.sleep(poll)

    if window is None:
        return False, (
            "WhatsApp did not start (WhatsApp.exe / WhatsApp.Root.exe) "
            "or window did not appear within 20 seconds."
        )

    _force_whatsapp_foreground(window)
    return True, "Successfully opened WhatsApp Desktop."


def _type_in_whatsapp_search(name):
    """
    Type a name into the WhatsApp search bar.
    Always clears the search field first regardless of existing text.
    """
    window = _get_whatsapp_window()
    if window:
        _force_whatsapp_foreground(window)
    time.sleep(0.5)

    # Step 1: Click the search field at the top of WhatsApp
    try:
        app = Application(backend="uia").connect(title_re=".*WhatsApp.*", timeout=3)
        win = app.window(title_re=".*WhatsApp.*")
        for _ in range(6):
            edits = win.descendants(control_type="Edit")
            if edits:
                edit = edits[0]
                edit.click_input()
                time.sleep(0.5)
                break
            time.sleep(1)
    except Exception as e:
        print(f"[WA] pywinauto click edit failed: {e}")
        # Fallback click on search area
        try:
            rect = window.rectangle() if window else None
            if rect:
                _safe_click(rect.left + 200, rect.top + 40)
            else:
                _safe_click(pyautogui.size()[0] // 4, 40)
            time.sleep(0.5)
        except Exception:
            pass

    # Step 2: Clear the search field thoroughly
    for _ in range(3):
        pyautogui.hotkey('ctrl', 'a')
        time.sleep(0.2)
    pyautogui.press('delete')
    time.sleep(0.3)
    pyautogui.hotkey('ctrl', 'a')
    time.sleep(0.2)
    pyautogui.press('delete')
    time.sleep(0.3)

    # Step 3: Type the contact name
    pyautogui.write(name, interval=0.06)
    time.sleep(2.5)

    # Step 4: Click on the first search result to open the chat
    # The search results appear below the search bar (roughly row height ~60px)
    rect = window.rectangle() if window else None
    if rect:
        _safe_click(rect.left + 200, rect.top + 120)
    else:
        _safe_click(pyautogui.size()[0] // 4, 120)
    time.sleep(1.5)
    pyautogui.press('enter')
    time.sleep(2)

    return True, "Contact searched."


def _verify_contact(window, name):
    """Verify the opened chat matches the target contact name."""
    try:
        for _ in range(10):
            # Check window title — WhatsApp updates it to contact name when chat opens
            try:
                import pygetwindow as gw
                active = gw.getActiveWindow()
                if active and name.lower() in active.title.lower():
                    print(f"[WA] Contact verified via window title: '{active.title}'")
                    return True
            except Exception:
                pass
            # Check header area by looking for Text descendants near top
            contact_ui = window.descendants(control_type="Text")
            for el in contact_ui:
                try:
                    el_text = el.window_text().strip().lower()
                    if name.lower() == el_text or name.lower() in el_text:
                        print(f"[WA] Contact verified: '{el_text}'")
                        return True
                except Exception:
                    continue
            time.sleep(1)
    except Exception:
        pass
    return False


def _is_whatsapp_text_field_focused():
    """Return True if the active element is a WhatsApp text input via pywinauto."""
    try:
        window = _get_whatsapp_window()
        if not window:
            return False
        focused = window.focused()
        if focused:
            ctrl = focused.element_info.control_type
            cls = focused.element_info.class_name or ""
            if ctrl == "Edit" or "Edit" in cls or "RichEdit" in cls:
                return True
        return False
    except Exception:
        return False


def _click_whatsapp_text_field(window):
    """Click the message input field at the bottom of the chat window."""
    try:
        edits = window.descendants(control_type="Edit")
        for edit in edits:
            try:
                rect = edit.rectangle()
                cx = (rect.left + rect.right) // 2
                cy = (rect.top + rect.bottom) // 2
                if not _safe_click(cx, cy):
                    continue
                time.sleep(0.5)
                return True
            except Exception:
                continue
    except Exception:
        pass
    # Fallback: click bottom area of WhatsApp window
    try:
        rect = window.rectangle()
        if not _safe_click(rect.left + rect.width // 2, rect.bottom - 80):
            return False
        time.sleep(0.5)
        return True
    except Exception:
        return False


def _verify_message_sent(window, message_text):
    """Verify the sent message bubble appears in the chat window."""
    if not message_text:
        return True
    try:
        # Wait briefly for the bubble to render
        time.sleep(1.0)
        # Try UI tree search for the sent message text
        for _ in range(5):
            try:
                descendants = window.descendants(control_type="Text")
                for el in descendants:
                    try:
                        el_text = el.window_text().strip()
                        if message_text.strip() in el_text:
                            print(f"[WA] Message bubble verified via UI tree: '{el_text[:50]}...'")
                            return True
                    except Exception:
                        continue
            except Exception:
                pass
            time.sleep(1)
        # Fallback: OCR on the chat area
        try:
            import pyautogui
            import pytesseract
            rect = window.rectangle()
            chat_region = (rect.left + 20, rect.top + 80, rect.width - 40, rect.height - 160)
            screenshot = pyautogui.screenshot(region=chat_region)
            ocr_text = pytesseract.image_to_string(screenshot)
            if message_text.strip().lower() in ocr_text.lower():
                print(f"[WA] Message bubble verified via OCR")
                return True
        except Exception:
            pass
    except Exception:
        pass
    print(f"[WA] Could not verify message bubble for '{message_text[:50]}...'")
    return False


# ══════════════════════════════════════════════════════════════════════════════
# SEARCH & LOGGING AUTOMATION FLOWS
# ══════════════════════════════════════════════════════════════════════════════

def search_and_summarize_in_notepad(query: str):
    """
    Iteratively searches DuckDuckGo, writes findings directly to a temporary file on the desktop,
    launches Notepad showing the file on screen, reads the data, summarizes using Groq LLM,
    closes Notepad, and deletes the temp file safely.
    """
    print(f"[SearchNotepad] Performing first search for '{query}'...")
    success1, res1 = smart_search(query)
    if not success1:
        res1 = "No initial search results fetched."
        
    detailed_query = f"{query} detailed breakdown summary"
    print(f"[SearchNotepad] Performing iterative search for '{detailed_query}'...")
    success2, res2 = smart_search(detailed_query)
    if not success2:
        res2 = "No additional details fetched."
        
    # Format the logged text nicely
    findings = (
        f"=== F.R.I.D.A.Y. SECURE INTEL SEARCH LOG ===\n"
        f"TARGET: {query}\n"
        f"TIMESTAMP: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"============================================\n\n"
        f"--- PHASE 1 FINDINGS ---\n{res1}\n\n"
        f"--- PHASE 2 FINDINGS ---\n{res2}\n\n"
        f"=== END OF DATA LOG ===\n"
    )
    
    # Save directly to a text file on the Desktop so Notepad opens it natively
    desktop = os.path.join(os.environ["USERPROFILE"], "Desktop")
    filepath = os.path.join(desktop, "friday_research.txt")
    
    try:
        # 1. Write the research file directly
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(findings)
            
        # 2. Launch Notepad with the file path natively
        print("[SearchNotepad] Launching Notepad with pre-populated file...")
        subprocess.Popen(["notepad.exe", filepath], shell=False)
        
        # 3. Leave it open on screen for 3 seconds for the user to see the high-tech log!
        time.sleep(3.0)
        
        # 4. Read back contents directly from the file (100% reliable, zero focus issues!)
        with open(filepath, "r", encoding="utf-8") as f:
            notepad_content = f.read()
            
        # 5. Call Groq LLM to summarize the findings
        import httpx
        groq_key = settings.GROQ_API_KEY
        if groq_key:
            payload = {
                "model": "llama-3.1-8b-instant",
                "messages": [
                    {
                        "role": "system", 
                        "content": (
                            __import__(
                                "brain.friday_persona", fromlist=["build_summarize_prompt"]
                            ).build_summarize_prompt()
                            + " Use the 5-step speech flow briefly. End with readiness for Boss."
                        )
                    },
                    {"role": "user", "content": notepad_content}
                ],
                "temperature": 0.3,
                "max_tokens": 200
            }
            with httpx.Client(timeout=10.0) as client:
                r = client.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": f"Bearer {groq_key}"}, json=payload)
                summary_text = r.json()["choices"][0]["message"]["content"].strip()
        else:
            summary_text = f"Here is the collected intelligence: {res1[:150]}..."
            
    except Exception as e:
        print(f"[SearchNotepad] Error during Notepad workflow: {e}")
        summary_text = f"I retrieved the search results, boss, but encountered a minor issue preparing the summary: {e}"
    finally:
        # 6. Close Notepad cleanly without saving dialogs
        os.system("taskkill /f /im notepad.exe")
        
        # 7. Delete the temporary file from the desktop so we don't leave clutter!
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass
            
    # Open default browser with Google Search so they have the browser search open as requested!
    search_google(query)
    
    return True, f"I have run an iterative search, logged it in Notepad, and closed Notepad as requested. Here is the summary, boss: {summary_text}"

# ══════════════════════════════════════════════════════════════════════════════
# EXISTING SYSTEM AUTOMATIONS
# ══════════════════════════════════════════════════════════════════════════════

def read_news_headlines(query: str):
    """Fetches top 3 headlines and summaries using Google News RSS."""
    import urllib.request
    import xml.etree.ElementTree as ET
    import html
    import re
    
    try:
        query = query.strip() or "top stories"
        encoded = urllib.parse.quote(query)
        rss_url = f"https://news.google.com/rss/search?q={encoded}&hl=en-IN&gl=IN&ceid=IN:en"
        req = urllib.request.Request(rss_url, headers={"User-Agent": "Mozilla/5.0"})
        
        with urllib.request.urlopen(req, timeout=7) as resp:
            xml_data = resp.read()
            
        root = ET.fromstring(xml_data)
        items = root.findall(".//item")
        
        output = []
        for i, item in enumerate(items[:3]):
            title = item.find("title").text.strip()
            title = re.sub(r' - [^-]+$', '', title)
            
            description = item.find("description").text or ""
            clean_desc = re.sub(r'<[^>]+>', '', description)
            clean_desc = html.unescape(clean_desc)
            summary = clean_desc.split(". ")[0].strip()
            if len(summary) > 100:
                summary = summary[:97] + "..."
                
            output.append(f"{i+1}. {title} — {summary}")
            
        if output:
            final_report = "Here are the latest headlines:\n" + "\n".join(output)
            return True, final_report
            
        raise Exception("No news found")
    except Exception as e:
        import urllib.parse
        encoded = urllib.parse.quote_plus(query + " news")
        open_url_in_arc(f"https://news.google.com/search?q={encoded}&hl=en-US&gl=US&ceid=US:en")
        return True, "Opening latest news for you."

def play_youtube(song):
    """Play a song on YouTube via direct watch URL."""
    from executor.music_player import play_on_youtube
    return play_on_youtube(song)

def search_google(query):
    """Opens Google search for the query."""
    if not query or not query.strip():
        return False, "Empty search query."
    try:
        encoded_query = urllib.parse.quote_plus(query)
        url = f"https://www.google.com/search?q={encoded_query}"
        open_url_in_arc(url)
        return True, f"Opened Google search for: {query}"
    except Exception as e:
        return False, f"Failed to open Google search: {str(e)}"

def smart_search(query: str):
    """
    Robust 3-tier search:
      1. DuckDuckGo Instant Answer API
      2. DuckDuckGo HTML regex scraping
      3. Direct Groq LLM knowledge answer (always works)
    """
    import httpx
    import re
    from html import unescape

    if not query or not query.strip():
        return False, "Empty search query."

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    }

    # ── Tier 1: DDG Instant Answer API ──────────────────────────────────────
    try:
        api_url = (
            "https://api.duckduckgo.com/?q="
            + urllib.parse.quote_plus(query)
            + "&format=json&no_html=1&skip_disambig=1"
        )
        with httpx.Client(headers=headers, timeout=6.0, follow_redirects=True) as client:
            data = client.get(api_url).json()

        abstract = data.get("AbstractText", "").strip()
        answer = data.get("Answer", "").strip()
        if abstract:
            return True, f"Here's what I found: {abstract}"
        if answer:
            return True, f"Here's what I found: {answer}"

        snippets = [
            t.get("Text", "")
            for t in data.get("RelatedTopics", [])
            if isinstance(t, dict) and t.get("Text")
        ]
        if snippets:
            combined = "\n".join(f"- {s}" for s in snippets[:3])
            return True, f"Based on my search, here is what I found:\n{combined}"
    except Exception as ex:
        print(f"[SmartSearch] DDG API failed: {ex}")

    # ── Tier 2: DDG HTML regex scraping ─────────────────────────────────────
    try:
        html_url = (
            "https://html.duckduckgo.com/html/?q="
            + urllib.parse.quote_plus(query)
        )
        with httpx.Client(headers=headers, timeout=8.0, follow_redirects=True) as client:
            html_text = client.get(html_url).text

        raw_snippets = re.findall(
            r'class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</(?:a|span|div)>',
            html_text,
            re.DOTALL | re.IGNORECASE,
        )
        clean = [
            unescape(re.sub(r"<[^>]+>", "", s)).strip()
            for s in raw_snippets
        ]
        clean = [c for c in clean if len(c) > 20][:3]
        if clean:
            return True, "Based on my search:\n" + "\n".join(f"- {c}" for c in clean)
    except Exception as ex:
        print(f"[SmartSearch] DDG HTML scrape failed: {ex}")

    # ── Tier 3: Direct Groq LLM answer ──────────────────────────────────────
    groq_key = settings.GROQ_API_KEY
    if not groq_key:
        return False, "I wasn't able to find an answer right now, boss."
    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": [
            {
                "role": "system",
                "content": (
                    __import__(
                        "brain.friday_persona", fromlist=["build_chat_system_prompt"]
                    ).build_chat_system_prompt()
                    + "\nAnswer in 2-4 short spoken sentences. No bullets or markdown."
                ),
            },
            {"role": "user", "content": query},
        ],
        "temperature": 0.3,
        "max_tokens": 220,
    }
    try:
        with httpx.Client(timeout=12.0) as client:
            r = client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}"},
                json=payload,
            )
            r.raise_for_status()
            answer = r.json()["choices"][0]["message"]["content"].strip()
            return True, answer
    except Exception as exc:
        print(f"[SmartSearch] Groq fallback error: {exc}")
        return False, "I wasn't able to fetch an answer right now, boss."

def play_yt_music(song):
    """Play a song on YouTube Music via direct watch URL."""
    from executor.music_player import play_on_youtube_music
    return play_on_youtube_music(song)


def smart_cleanup_tabs(close_keywords=None, keep_keywords=None):
    """
    Intelligently closes browser tabs that aren't needed (duplicates, blank tabs,
    old search result tabs, or tabs matching/not matching requested keywords).
    """
    import pywinauto
    from pywinauto import Desktop
    import pyautogui
    import time

    browsers = ["chrome", "msedge", "brave", "firefox"]
    desktop = Desktop(backend="uia")
    
    # We retrieve all visible windows
    try:
        windows = desktop.windows(visible_only=True)
    except Exception as e:
        print(f"[TabCleanup] Failed to list windows: {e}")
        return 0

    closed_count = 0
    seen_titles = set()
    
    # Standardize keywords
    close_keywords = [k.lower().strip() for k in (close_keywords or []) if k.strip()]
    keep_keywords = [k.lower().strip() for k in (keep_keywords or []) if k.strip()]

    for win in windows:
        try:
            win_title = win.window_text().lower()
        except Exception:
            continue
            
        if not any(b in win_title for b in browsers):
            continue

        try:
            # Enumerate TabItems in this browser window
            tabs = win.descendants(control_type="TabItem")
            if not tabs:
                continue

            for tab in list(tabs):
                try:
                    tab_title = tab.window_text()
                except Exception:
                    continue

                tab_title_lower = tab_title.lower()
                should_close = False
                reason = ""

                # Heuristic 1: Blank or New Tabs
                if tab_title_lower in ["new tab", "about:blank", "blank"]:
                    should_close = True
                    reason = "blank tab"
                # Heuristic 2: Specific close keywords targeted
                elif close_keywords and any(k in tab_title_lower for k in close_keywords):
                    should_close = True
                    reason = "close keyword match"
                # Heuristic 3: Specific keep keywords targeted (close if it doesn't match any)
                elif keep_keywords and not any(k in tab_title_lower for k in keep_keywords):
                    should_close = True
                    reason = "not in keep keywords"
                # Heuristic 4: Duplicate tabs
                elif tab_title_lower in seen_titles:
                    should_close = True
                    reason = "duplicate"
                # Heuristic 5: Duplicate search result pages
                elif "google search" in tab_title_lower or "duckduckgo" in tab_title_lower:
                    search_key = "google_search" if "google" in tab_title_lower else "ddg_search"
                    if search_key in seen_titles:
                        should_close = True
                        reason = "old search results"
                    else:
                        seen_titles.add(search_key)
                
                if not should_close:
                    seen_titles.add(tab_title_lower)
                    continue

                print(f"[TabCleanup] Closing tab '{tab_title}' (Reason: {reason})")
                
                # Try finding close button within the tab item
                closed = False
                try:
                    close_buttons = tab.descendants(control_type="Button")
                    for btn in close_buttons:
                        btn_name = (btn.element_info.name or "").lower()
                        # Often the close button is named "Close" or "Close tab" or contains 'x'
                        if "close" in btn_name or btn.window_text().lower() == "x" or "close tab" in btn_name:
                            try:
                                btn.invoke()
                            except Exception:
                                btn.click_input()
                            closed = True
                            closed_count += 1
                            time.sleep(0.15)
                            break
                except Exception as ex:
                    print(f"[TabCleanup] Failed close button click: {ex}")

                if not closed:
                    # Fallback: select tab and send ctrl+w
                    try:
                        tab.select()
                        time.sleep(0.15)
                        pyautogui.hotkey('ctrl', 'w')
                        closed_count += 1
                        time.sleep(0.15)
                    except Exception as ex:
                        print(f"[TabCleanup] Fallback select+ctrl+w failed: {ex}")
                        
        except Exception as e:
            print(f"[TabCleanup] Error enumerating tabs in browser window '{win_title}': {e}")
            
    return closed_count
