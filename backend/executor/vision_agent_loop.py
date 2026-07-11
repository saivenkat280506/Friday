import asyncio
import json
import base64
import io
import os
import sys
import httpx
import pyautogui
from brain.groq_decision import decide_action
from executor.tool_executor import execute_tool

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

def capture_screen_base64() -> str:
    """Captures primary screen and converts to base64 PNG string."""
    try:
        screenshot = pyautogui.screenshot()
        buffered = io.BytesIO()
        screenshot.save(buffered, format="PNG")
        return base64.b64encode(buffered.getvalue()).decode()
    except Exception as e:
        print(f"[VisionAgent Capture Failed] {e}")
        return ""

class VisionAgent:
    def __init__(self):
        self.last_desc = ""
        self.action_history = []
        self.queue = asyncio.Queue()
        self.local_loop_active = False
        self.current_user_intent = None
        self.capture_interval = 15  # seconds between passive captures (lighter on 16GB RAM)

    async def run(self):
        print("[VisionAgent] Continuous monitoring active and listening to dispatch queue...")
        # Start queue reader task
        asyncio.create_task(self.dispatch_loop())
        
        # Optional local screenshot capture loop (can be toggled)
        if self.local_loop_active:
            asyncio.create_task(self.run_local_vision_loop())

    async def dispatch_loop(self):
        while True:
            try:
                description = await self.queue.get()
                await self.handle_vision(description, user_intent=self.current_user_intent)
                self.queue.task_done()
            except Exception as e:
                print(f"[VisionAgent Dispatch Error] {e}")
            # Drain backlog quickly; idle poll stays light
            await asyncio.sleep(0.05 if not self.queue.empty() else 0.4)

    async def enqueue_vision(self, description: str):
        """Enqueues new description received from extension or local loop."""
        await self.queue.put(description)

    async def analyze_current_screen(self) -> str:
        """On-demand screen capture + analysis."""
        try:
            from vision.capture import capture_screen_base64
            from vision.vision_analyzer import analyze_screen
            b64 = capture_screen_base64(draw_boxes=False)
            if not b64:
                return "Screen capture failed."
            desc = analyze_screen(b64)
            self.last_desc = desc
            return desc
        except Exception as e:
            print(f"[VisionAgent] On-demand analysis failed: {e}")
            return f"Screen analysis unavailable: {e}"

    async def run_local_vision_loop(self):
        """Continuous local screenshot analysis loop using vision_analyzer."""
        print("[VisionAgent] Local screen capturing loop active.")
        while self.local_loop_active:
            try:
                from brain.context_manager import is_resource_constrained
                if is_resource_constrained(ram_threshold=85.0):
                    await asyncio.sleep(max(self.capture_interval, 30))
                    continue
                desc = await asyncio.to_thread(self._capture_and_analyze)
                if desc and desc != self.last_desc:
                    print(f"[VisionAgent] Local capture analysis: {desc[:100]}...")
                    await self.enqueue_vision(desc)
            except Exception as e:
                print(f"[VisionAgent Local Loop Warning] {e}")
            await asyncio.sleep(self.capture_interval)

    def _capture_and_analyze(self) -> str:
        """Synchronous helper for capture + analyze."""
        try:
            from vision.capture import capture_screen_base64
            from vision.vision_analyzer import analyze_screen
            b64 = capture_screen_base64(draw_boxes=False)
            if not b64:
                return ""
            return analyze_screen(b64)
        except Exception as e:
            print(f"[VisionAgent] Capture/analyze error: {e}")
            return ""

    async def handle_vision(self, description: str, user_intent: str = None):
        """Processes incoming screen description, makes a Groq decision, and triggers tools."""
        if not description or description.strip() == self.last_desc:
            return
        
        self.last_desc = description.strip()
        print(f"[Vision] New screen description (first 200 chars): {description[:200]}")
        
        # Smart intent resolution using action history for commands like "again" or "repeat"
        resolved_intent = user_intent
        if self.action_history and user_intent and any(keyword in user_intent.lower() for keyword in ["again", "repeat", "redo"]):
            last_action = self.action_history[-1]
            resolved_intent = f"{user_intent} (Context: Previous resolved action was '{last_action.get('tool')}' with params {last_action.get('params')})"
            print(f"[VisionAgent] Resolved 'again' context to: {resolved_intent}")
            
        action_raw = decide_action(description, resolved_intent)
        print(f"[Groq decision] {action_raw}")
        
        # Clean markdown or wrapping formatting if present
        cleaned_action = action_raw.strip()
        if cleaned_action.startswith("```json"):
            cleaned_action = cleaned_action.split("```json")[1].split("```")[0].strip()
        elif cleaned_action.startswith("```"):
            cleaned_action = cleaned_action.split("```")[1].split("```")[0].strip()
            
        try:
            action_json = json.loads(cleaned_action)
        except json.JSONDecodeError:
            print(f"[Error] Could not parse JSON action: {cleaned_action}")
            return
            
        if action_json.get("action") == "none":
            print("[Agent] No action needed. Clearing active user intent.")
            self.current_user_intent = None
            return
            
        tool_name = action_json.get("tool")
        params = action_json.get("params", {})
        
        if tool_name:
            print(f"[Agent] Executing {tool_name} with parameters: {params}")
            tool_dict = {
                "intent": tool_name,
                "parameters": params
            }
            # Execute as an awaited coroutine conforming with execute_tool
            success, result = await execute_tool(tool_dict)
            print(f"[Result] Status: {success}, Details: {result}")
            self.action_history.append(action_json)
        else:
            print(f"[Warning] Unknown action format or missing tool name: {action_json}")
