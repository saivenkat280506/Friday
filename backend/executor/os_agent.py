"""
os_agent.py — Autonomous Vision-Powered OS Agent
=================================================
Uses screenshot-based vision (Groq multimodal) to SEE the screen,
then issues precise actions to control the entire Windows desktop.

This is the core of FRIDAY's autonomous control system.
"""

import pyautogui
import pywinauto
import time
import re
import os
import subprocess
import base64
import json
import mss
from io import BytesIO
from PIL import Image
from langchain_groq import ChatGroq
from langchain_core.messages import SystemMessage, HumanMessage

# Keep PyAutoGUI's emergency corner fail-safe enabled. Movement helpers below
# avoid exact corners so automation stays interruptible without random failures.
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05

# ── Screen info ────────────────────────────────────────────────────────────────

# Resolution the LLM sees in the screenshot (we resize to this for fast inference)
_LLM_WIDTH = 1280
_LLM_HEIGHT = 720


def _get_screen_geometry():
    """Return the primary monitor origin and resolution."""
    with mss.mss() as sct:
        mon = sct.monitors[1]
        return mon["left"], mon["top"], mon["width"], mon["height"]


def _get_screen_size():
    """Return the actual primary monitor resolution."""
    _, _, width, height = _get_screen_geometry()
    return width, height


def _scale_coords(x: int, y: int) -> tuple:
    """
    Scale coordinates from the LLM screenshot space (1280x720) to the
    actual screen resolution.  If the LLM returns coords that already
    look like real-screen values (e.g. > LLM_WIDTH), leave them alone.
    """
    left, top, real_w, real_h = _get_screen_geometry()

    # Heuristic: if both coords fit inside the LLM viewport, scale them up.
    # If they already exceed it, they probably came from the UI tree and are
    # already in real-screen space.
    if x <= _LLM_WIDTH and y <= _LLM_HEIGHT:
        sx = left + int(x * real_w / _LLM_WIDTH)
        sy = top + int(y * real_h / _LLM_HEIGHT)
        return _clamp_to_primary_monitor(sx, sy)
    return _clamp_to_primary_monitor(x, y)


def _clamp_to_primary_monitor(x: int, y: int) -> tuple[int, int]:
    """Keep actions on the visible primary monitor and away from fail-safe corners."""
    left, top, width, height = _get_screen_geometry()
    margin = 3
    min_x = left + margin
    min_y = top + margin
    max_x = left + width - margin - 1
    max_y = top + height - margin - 1
    return max(min_x, min(x, max_x)), max(min_y, min(y, max_y))


# ── Screenshot Engine ──────────────────────────────────────────────────────────

def capture_screenshot_b64(quality: int = 70) -> str:
    """Capture the full screen, compress, and return base64-encoded JPEG."""
    with mss.mss() as sct:
        monitor = sct.monitors[1]
        sct_img = sct.grab(monitor)
        img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
        # Resize to the exact coordinate space promised to the LLM.
        img = img.resize((_LLM_WIDTH, _LLM_HEIGHT), Image.Resampling.LANCZOS)
        buffer = BytesIO()
        img.save(buffer, format="JPEG", quality=quality)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")


def get_os_virtual_dom() -> str:
    """
    Scans the Desktop using pywinauto UIAutomation to create a lightweight
    'Virtual DOM' of visible windows and their child elements.
    Coordinates are reported in LLM-screenshot space so the LLM can
    correlate them with what it sees in the image.
    """
    left, top, real_w, real_h = _get_screen_geometry()

    def _to_llm(val_x, val_y):
        """Convert real-screen coords to LLM screenshot coords."""
        return (
            int((val_x - left) * _LLM_WIDTH / real_w),
            int((val_y - top) * _LLM_HEIGHT / real_h),
        )

    try:
        desktop = pywinauto.Desktop(backend="uia")
        windows = desktop.windows(visible_only=True)

        dom_lines = []
        for i, win in enumerate(windows):
            title = win.window_text()
            if not title or "Friday" in title or "Taskbar" in title:
                continue

            rect = win.rectangle()
            if rect.width() < 50 or rect.height() < 50:
                continue

            tl = _to_llm(rect.left, rect.top)
            br = _to_llm(rect.right, rect.bottom)
            dom_lines.append(
                f"[{i}] WINDOW: '{title}' at ({tl[0]},{tl[1]},{br[0]},{br[1]})"
            )

            try:
                children = win.children()
                for c_idx, child in enumerate(children[:20]):
                    c_title = child.window_text()
                    c_type = child.friendly_class_name()
                    c_rect = child.rectangle()
                    if c_title and c_rect.width() > 10:
                        cx, cy = _to_llm(
                            (c_rect.left + c_rect.right) // 2,
                            (c_rect.top + c_rect.bottom) // 2,
                        )
                        bl = _to_llm(c_rect.left, c_rect.top)
                        bbr = _to_llm(c_rect.right, c_rect.bottom)
                        dom_lines.append(
                            f"    [{i}.{c_idx}] {c_type}: '{c_title}' "
                            f"center=({cx},{cy}) bounds=({bl[0]},{bl[1]},{bbr[0]},{bbr[1]})"
                        )
            except Exception:
                pass

        return "\n".join(dom_lines) if dom_lines else "No major windows visible on screen."
    except Exception as e:
        return f"Error scanning UI: {e}"


# ── Smooth mouse helpers ───────────────────────────────────────────────────────

def _smooth_move(x: int, y: int, duration: float = 0.5):
    """Move the cursor smoothly so the user can see it tracking."""
    x, y = _clamp_to_primary_monitor(x, y)
    pyautogui.moveTo(x, y, duration=duration, tween=pyautogui.easeInOutQuad)


def _smooth_click(x: int, y: int):
    """Move smoothly to (x,y), pause briefly, then click."""
    _smooth_move(x, y)
    time.sleep(0.15)
    pyautogui.click()


def _smooth_double_click(x: int, y: int):
    _smooth_move(x, y)
    time.sleep(0.15)
    pyautogui.doubleClick()


def _smooth_right_click(x: int, y: int):
    _smooth_move(x, y)
    time.sleep(0.15)
    pyautogui.rightClick()


# ── Action Executor ────────────────────────────────────────────────────────────

def _typing_blocked_message() -> str | None:
    """Return an error message when focus is not on a text input; None if typing is safe."""
    try:
        from executor.window_context import validate_typing_context
        check = validate_typing_context({}, intent="type_text")
        if check.get("status") != "ok":
            return f"TYPE blocked: {check.get('message', 'Focus is not on a text field')}"
    except Exception as e:
        return f"TYPE blocked: could not verify text field focus ({e})"
    return None


def execute_os_action(action_str: str) -> str:
    """
    Execute a command string from the LLM.
    Supported commands:
      CLICK(x, y)
      DOUBLE_CLICK(x, y)
      RIGHT_CLICK(x, y)
      TYPE("text")
      TYPE_SLOW("text")                 — types character-by-character with delay
      HOTKEY("ctrl", "t")
      PRESS("enter")
      SCROLL(x, y, clicks)             — scroll at position (negative = down)
      MOVE(x, y)                        — move mouse
      WAIT(seconds)
      OPEN_URL("https://...")           — opens URL in Chrome
      RUN_CMD("command string")         — runs a shell command
      SCREENSHOT()                      — returns that a screenshot was taken
      DONE("summary of what was done")
    """
    cmd = action_str.strip()
    cmd_upper = cmd.upper()
    try:
        # ── CLICK ──
        if cmd_upper.startswith("CLICK"):
            coords = re.findall(r'-?\d+', cmd)
            if len(coords) >= 2:
                x, y = _scale_coords(int(coords[0]), int(coords[1]))
                _smooth_click(x, y)
                return f"Clicked at ({x}, {y})"
            return "CLICK requires (x, y) coordinates"

        # ── DOUBLE_CLICK ──
        elif cmd_upper.startswith("DOUBLE_CLICK"):
            coords = re.findall(r'-?\d+', cmd)
            if len(coords) >= 2:
                x, y = _scale_coords(int(coords[0]), int(coords[1]))
                _smooth_double_click(x, y)
                return f"Double-clicked at ({x}, {y})"

        # ── RIGHT_CLICK ──
        elif cmd_upper.startswith("RIGHT_CLICK"):
            coords = re.findall(r'-?\d+', cmd)
            if len(coords) >= 2:
                x, y = _scale_coords(int(coords[0]), int(coords[1]))
                _smooth_right_click(x, y)
                return f"Right-clicked at ({x}, {y})"

        # ── TYPE_SLOW (character by character for search bars etc.) ──
        elif cmd_upper.startswith("TYPE_SLOW"):
            content = re.search(r'TYPE_SLOW\("(.+?)"\)', cmd, re.DOTALL)
            if content:
                blocked = _typing_blocked_message()
                if blocked:
                    return blocked
                text = content.group(1)
                # Clear field before typing
                pyautogui.hotkey('ctrl', 'a')
                time.sleep(0.05)
                pyautogui.press('delete')
                time.sleep(0.05)
                pyautogui.write(text, interval=0.06)
                return f"Slowly typed '{text}'"

        # ── TYPE ──
        elif cmd_upper.startswith("TYPE"):
            content = re.search(r'TYPE\("(.+?)"\)', cmd, re.DOTALL)
            if content:
                blocked = _typing_blocked_message()
                if blocked:
                    return blocked
                text = content.group(1)
                # Clear field before typing/pasting
                pyautogui.hotkey('ctrl', 'a')
                time.sleep(0.05)
                pyautogui.press('delete')
                time.sleep(0.05)
                # Use pyperclip + ctrl+v for reliability with special chars
                try:
                    import pyperclip
                    pyperclip.copy(text)
                    pyautogui.hotkey('ctrl', 'v')
                    time.sleep(0.2)
                    return f"Typed '{text}'"
                except ImportError:
                    pyautogui.write(text, interval=0.02)
                    return f"Typed '{text}'"

        # ── PRESS ──
        elif cmd_upper.startswith("PRESS"):
            key_match = re.search(r'PRESS\("(.+?)"\)', cmd)
            if key_match:
                key = key_match.group(1).lower()
                pyautogui.press(key)
                return f"Pressed '{key}'"

        # ── HOTKEY ──
        elif cmd_upper.startswith("HOTKEY"):
            keys = re.findall(r'"([^"]+)"', cmd)
            if keys:
                pyautogui.hotkey(*[k.lower() for k in keys])
                return f"Pressed hotkey {keys}"

        # ── SCROLL ──
        elif cmd_upper.startswith("SCROLL"):
            nums = re.findall(r'-?\d+', cmd)
            if len(nums) >= 3:
                x, y = _scale_coords(int(nums[0]), int(nums[1]))
                clicks = int(nums[2])
                _smooth_move(x, y, duration=0.2)
                pyautogui.scroll(clicks)
                return f"Scrolled {clicks} clicks at ({x}, {y})"
            elif len(nums) >= 1:
                pyautogui.scroll(int(nums[0]))
                return f"Scrolled {nums[0]} clicks"

        # ── MOVE ──
        elif cmd_upper.startswith("MOVE"):
            coords = re.findall(r'-?\d+', cmd)
            if len(coords) >= 2:
                x, y = _scale_coords(int(coords[0]), int(coords[1]))
                _smooth_move(x, y)
                return f"Moved mouse to ({x}, {y})"

        # ── WAIT ──
        elif cmd_upper.startswith("WAIT"):
            secs = re.findall(r'[\d.]+', cmd)
            wait_time = float(secs[0]) if secs else 2
            wait_time = min(wait_time, 10)  # Cap at 10 seconds
            time.sleep(wait_time)
            return f"Waited {wait_time}s"

        # ── OPEN_URL ──
        elif cmd_upper.startswith("OPEN_URL"):
            url_match = re.search(r'OPEN_URL\("(.+?)"\)', cmd)
            if url_match:
                url = url_match.group(1)
                try:
                    from executor.automation import open_url_in_chrome
                except ImportError:
                    from automation import open_url_in_chrome
                open_url_in_chrome(url)
                return f"Opened URL: {url}"

        # ── RUN_CMD ──
        elif cmd_upper.startswith("RUN_CMD"):
            cmd_match = re.search(r'RUN_CMD\("(.+?)"\)', cmd)
            if cmd_match:
                shell_cmd = cmd_match.group(1)
                result = subprocess.run(shell_cmd, shell=True, capture_output=True, text=True, timeout=15)
                output = result.stdout.strip() or result.stderr.strip() or "Command completed"
                return f"Command result: {output[:500]}"

        # ── SCREENSHOT ──
        elif cmd_upper.startswith("SCREENSHOT"):
            return "Screenshot captured — analyzing screen state"

        # ── DONE ──
        elif cmd_upper.startswith("DONE"):
            msg = re.search(r'DONE\("(.+?)"\)', cmd, re.DOTALL)
            return f"TASK_COMPLETE: {msg.group(1) if msg else 'Task finished'}"

        return f"Unknown command format: {cmd}"
    except Exception as e:
        return f"Action failed: {e}"


# ── Main Agent Loop ────────────────────────────────────────────────────────────

# Maximum recent messages the LLM sees (system + last N exchanges).
# Keeps context focused and avoids token-limit blowouts from accumulated images.
_MAX_HISTORY = 6


def run_os_agent(task_description: str, max_steps: int = 15, use_vision: bool = True) -> str:
    """
    Autonomous agent loop that:
    1. Takes a screenshot of the screen (vision mode) OR reads the UI tree
    2. Sends it to the LLM with the task description
    3. Receives the next action command
    4. Executes it
    5. Repeats until DONE or max_steps reached

    Returns the final summary string.
    """

    # Use vision model for screenshot-based control
    if use_vision:
        try:
            llm_vision = ChatGroq(
                model_name="llama-3.2-11b-vision-preview",
                temperature=0.1,
                max_tokens=1024,
            )
        except Exception:
            use_vision = False

    # Fallback text-only model
    llm_text = ChatGroq(model_name="llama-3.3-70b-versatile", temperature=0.1)

    real_w, real_h = _get_screen_size()

    from brain.friday_persona import build_agent_system_prompt

    system_prompt = build_agent_system_prompt(task_description) + f"""

You are operating with FULL CONTROL of a Windows PC.

You can SEE the screen via screenshots and a UI element tree.
The screenshot is {_LLM_WIDTH}x{_LLM_HEIGHT} pixels. ALL coordinates in the UI tree and screenshot are in this {_LLM_WIDTH}x{_LLM_HEIGHT} space.
Your job: issue ONE command per turn to accomplish the task step-by-step.
When you specify (x, y) coordinates, use the {_LLM_WIDTH}x{_LLM_HEIGHT} coordinate space you see in the screenshot and UI tree.

AVAILABLE COMMANDS:
1.  CLICK(x, y)              — Left click at screen coordinates
2.  DOUBLE_CLICK(x, y)       — Double click at screen coordinates
3.  RIGHT_CLICK(x, y)        — Right click at screen coordinates
4.  TYPE("text")              — Type text (uses clipboard paste - fast & reliable)
5.  TYPE_SLOW("text")         — Type text char-by-char (for search boxes that filter as you type)
6.  PRESS("key")              — Press a single key: enter, tab, escape, backspace, space, f5, etc.
7.  HOTKEY("ctrl", "t")       — Press keyboard shortcut combo
8.  SCROLL(x, y, clicks)     — Scroll at position. Negative clicks = scroll down, positive = scroll up
9.  MOVE(x, y)                — Move mouse cursor to coordinates
10. WAIT(seconds)             — Wait/pause (max 10s)
11. OPEN_URL("https://...")   — Open a URL in Chrome
12. RUN_CMD("command")        — Run a shell/PowerShell command
13. SCREENSHOT()              — Take a fresh screenshot (to check current state)
14. DONE("summary")           — Finish the task with a summary

CRITICAL RULES:
- Output ONLY the single command. No explanation, no extra text.
- ALL coordinates must be in the {_LLM_WIDTH}x{_LLM_HEIGHT} screenshot coordinate space.
- For clicking buttons/links: use the CENTER coordinates from the UI tree or estimate from the screenshot.
- When typing in a browser's address bar: first CLICK the address bar, then TYPE the URL, then PRESS("enter").
- For YouTube: OPEN_URL("https://www.youtube.com/results?search_query=YOUR+SEARCH") to search directly.
- For playing a video: after search results load, CLICK on the video thumbnail.
- When writing code: open the appropriate editor first, then TYPE the code.
- If something doesn't seem to work, try an alternative approach.
- ALWAYS end with DONE("description of what was accomplished") when the task is complete.
- If the task is simple (e.g., open a URL), you can do it in 1-2 steps. Don't over-complicate.
- Use RUN_CMD for system operations like creating files, running scripts, checking status.
- Be PRECISE with coordinates. Look at the UI tree element centers for accurate targeting.

SHORTCUTS YOU KNOW:
- Win+R: Run dialog
- Ctrl+T: New browser tab
- Ctrl+L / F6: Focus address bar in browser
- Ctrl+W: Close current tab
- Alt+F4: Close window
- Win+E: Open File Explorer
- Ctrl+C/V/X: Copy/Paste/Cut
- Ctrl+S: Save
- Ctrl+Z: Undo
"""

    sys_msg = SystemMessage(content=system_prompt)
    messages = [sys_msg]
    action_history = []

    for step in range(max_steps):
        time.sleep(0.8)  # Let screen settle

        # Build the observation context
        ui_tree = get_os_virtual_dom()

        if use_vision:
            try:
                screenshot_b64 = capture_screenshot_b64()
                observation_msg = HumanMessage(content=[
                    {
                        "type": "text",
                        "text": (
                            f"Step {step + 1}/{max_steps}. Task: {task_description}\n\n"
                            f"UI Element Tree (coords in {_LLM_WIDTH}x{_LLM_HEIGHT} space):\n{ui_tree}\n\n"
                            f"Previous actions taken: {json.dumps(action_history[-5:]) if action_history else 'None yet'}\n\n"
                            "I've attached a screenshot of the current screen. "
                            "Analyze everything visible and issue your NEXT command. "
                            "Output ONLY the command, nothing else."
                        )
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{screenshot_b64}"
                        }
                    }
                ])
                messages.append(observation_msg)

                # Trim history to keep context window manageable
                # Keep: system message + last _MAX_HISTORY messages
                if len(messages) > _MAX_HISTORY + 1:
                    messages = [sys_msg] + messages[-_MAX_HISTORY:]

                response = llm_vision.invoke(messages)
            except Exception as e:
                print(f"[OS Agent] Vision call failed: {e}, falling back to text")
                # Create a copy of messages and sanitize for text-only model
                sanitized_messages = [sys_msg]
                for m in messages[1:]:
                    if isinstance(m, HumanMessage) and isinstance(m.content, list):
                        text_content = next(
                            (item["text"] for item in m.content if item.get("type") == "text"), ""
                        )
                        sanitized_messages.append(
                            HumanMessage(content=f"[Screenshot Data Stripped] {text_content}")
                        )
                    else:
                        sanitized_messages.append(m)

                observation_msg = HumanMessage(content=(
                    f"Step {step + 1}/{max_steps}. Task: {task_description}\n\n"
                    f"UI Element Tree:\n{ui_tree}\n\n"
                    f"Previous actions: {json.dumps(action_history[-5:]) if action_history else 'None'}\n\n"
                    "Issue your next command. Output ONLY the command."
                ))
                sanitized_messages.append(observation_msg)
                if len(sanitized_messages) > _MAX_HISTORY + 1:
                    sanitized_messages = [sys_msg] + sanitized_messages[-_MAX_HISTORY:]
                response = llm_text.invoke(sanitized_messages)
        else:
            observation_msg = HumanMessage(content=(
                f"Step {step + 1}/{max_steps}. Task: {task_description}\n\n"
                f"UI Element Tree:\n{ui_tree}\n\n"
                f"Previous actions: {json.dumps(action_history[-5:]) if action_history else 'None'}\n\n"
                "Issue your next command. Output ONLY the command."
            ))
            messages.append(observation_msg)
            if len(messages) > _MAX_HISTORY + 1:
                messages = [sys_msg] + messages[-_MAX_HISTORY:]
            response = llm_text.invoke(messages)

        cmd = response.content.strip()
        # Clean up any markdown or extra text
        cmd = cmd.replace("```", "").strip()
        # Take only the first line if multiple
        if "\n" in cmd:
            cmd = cmd.split("\n")[0].strip()

        messages.append(response)

        print(f"[OS Agent] Step {step + 1}: {cmd}")

        # Check for DONE
        if cmd.upper().startswith("DONE"):
            msg = re.search(r'DONE\("(.+?)"\)', cmd, re.DOTALL)
            summary = msg.group(1) if msg else "Task completed"
            try:
                from executor.window_manager import bring_friday_to_front
                bring_friday_to_front()
            except Exception as e:
                print(f"Failed to refocus FRIDAY window: {e}")
            return f"SUCCESS: {summary}"

        # Execute the action
        result = execute_os_action(cmd)
        action_history.append({"step": step + 1, "command": cmd, "result": result})

        # Check if execute_os_action returned a TASK_COMPLETE
        if "TASK_COMPLETE" in result:
            try:
                from executor.window_manager import bring_friday_to_front
                bring_friday_to_front()
            except Exception as e:
                print(f"Failed to refocus FRIDAY window: {e}")
            return result.replace("TASK_COMPLETE: ", "SUCCESS: ")

        # Feed result back
        messages.append(HumanMessage(content=f"Action result: {result}"))

        print(f"[OS Agent] Result: {result}")

    try:
        from executor.window_manager import bring_friday_to_front
        bring_friday_to_front()
    except Exception as e:
        print(f"Failed to refocus FRIDAY window: {e}")
    return "Task ended after reaching maximum steps. Partial progress may have been made."
