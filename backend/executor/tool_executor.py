"""
tool_executor.py — Async Execution Engine with Feedback Loop
===========================================================
Validates and executes tools based on LLM decisions with async support,
timeout protection, and failure recovery.
"""

import asyncio
import json
import functools
from executor.tools_registry import get_tool
from brain.safety import validate_action
from brain.memory import save_memory
from executor.task_manager import task_manager
from executor.agent_loop import agent_loop
from executor.error_handler import log_error
from executor.window_context import WindowContextChecker
from brain.memory_store import get_memory_store

# ── Window context checker (pre-flight safety) ──────────────────────────────────
_window_checker = WindowContextChecker()

CHECKED_INTENTS = {"type_text", "click_at", "move_mouse", "hotkey"}
TYPING_GUARD_INTENTS = {"type_text"}

# Default timeout for tool execution (seconds)
TOOL_TIMEOUT = 45

async def execute_tool(action_json: dict, background: bool = False):
    """
    Validates the JSON and executes the mapped tool with timeout protection.
    """
    # 1. Safety Check
    is_safe, safe_json = validate_action(action_json)
    if not is_safe:
        save_memory("last_status", "invalid_action")
        return False, "Invalid action requested. Falling back to search."
    
    intent = safe_json.get("intent")
    params = safe_json.get("parameters", {})
    
    # Keep short-term memory up-to-date for pronoun resolution
    if intent in ("play_music", "play_youtube_music", "play_spotify_music", "play_youtube") and params.get("song"):
        save_memory("last_song", params.get("song"))
    elif intent == "send_whatsapp" and (params.get("contact") or params.get("name")):
        save_memory("last_contact", params.get("contact") or params.get("name"))
        
    if intent == "chat":
        return True, params.get("response", "I am here.")

    # 1.5 Window Context Check (pre-flight safety)
    if intent in CHECKED_INTENTS or intent in TYPING_GUARD_INTENTS:
        check_result = _window_checker.pre_flight(intent, params)
        if check_result["status"] != "ok":
            behavior = check_result.get("behavior", WindowContextChecker.BEHAVIOR_HARD_STOP)
            msg = check_result.get("message", "Window context mismatch")
            print(f"[Executor] Window context check failed: {msg}")
            save_memory("last_status", "window_context_mismatch")
            if behavior == WindowContextChecker.BEHAVIOR_HOLD_AND_ASK:
                return False, f"{msg} Please click the correct text field and try again."
            return False, msg
        if check_result.get("behavior") == WindowContextChecker.BEHAVIOR_AUTO_CORRECT:
            print(f"[Executor] Auto-corrected window focus for {intent}")

    # 2. Resolve Tool
    tool_func = get_tool(intent)
    if not tool_func:
        save_memory("last_status", "tool_not_found")
        return False, f"Tool '{intent}' not found in registry."
    
    # 3. Define Execution Wrapper for Task Manager
    async def _run():
        try:
            current_timeout = 75 if intent == "send_whatsapp" else TOOL_TIMEOUT

            if intent == "send_whatsapp":
                from executor.whatsapp_handler import send_whatsapp_message as wa_send

                contact = params.get("contact") or params.get("name", "")
                wa_result = await asyncio.wait_for(
                    wa_send(contact, params.get("message", "")),
                    timeout=current_timeout,
                )
                success = bool(wa_result.get("success"))
                result = (
                    f"Message sent to {wa_result.get('contact', '')} on WhatsApp."
                    if success
                    else (wa_result.get("error") or "Failed to send WhatsApp message.")
                )
            else:
                # Most tools are sync — wrap in to_thread with timeout protection
                success, result = await asyncio.wait_for(
                    asyncio.to_thread(tool_func, params),
                    timeout=current_timeout,
                )
            
            save_memory("last_result", result)
            save_memory("last_status", "success" if success else "failure")
            
            # Store episode in ChromaDB memory
            _store_episode(intent, params, result, success)
            
            if not success:
                # Log the actual error internally
                log_error(intent, Exception(result))
                
                # --- Failure Recovery Logic ---
                if intent == "send_whatsapp":
                    print("[Executor] WhatsApp send failed. Falling back to opening app.")
                    from executor.open_app import open_app
                    f_success, f_msg = await asyncio.to_thread(open_app, "whatsapp")
                    if f_success:
                        return True, "I couldn't send the message automatically, but I've opened WhatsApp for you."
                
                if intent != "search_browser":
                    print("[Executor] Tool failed. Falling back to browser search.")
                    from executor.automation import search_google
                    s_success, s_msg = await asyncio.to_thread(search_google, params.get("query", "the request"))
                    if s_success:
                        return True, "Automatic tool failed, but I've searched the web for you instead."
                
                # Final fallback: add to retry queue in agent_loop
                # Use functools.partial to avoid stale closure reference
                retry_func = functools.partial(_retry_tool, tool_func, params)
                agent_loop.add_to_retry_queue(
                    retry_func, 
                    {"name": intent, "params": params}
                )
                return False, result
            
            return success, result

        except asyncio.TimeoutError:
            current_timeout = 75 if intent == "send_whatsapp" else TOOL_TIMEOUT
            save_memory("last_status", "timeout")
            log_error(intent, Exception(f"Tool '{intent}' timed out after {current_timeout}s"))
            return False, f"Tool '{intent}' timed out after {current_timeout} seconds."

        except Exception as e:
            save_memory("last_status", "exception")
            log_error(intent, e)
            return False, f"Execution error in {intent}: {str(e)}"

    # 4. Handle Blocking vs Background
    if background:
        task_id = await task_manager.start_task(_run(), name=f"BG_{intent}")
        return True, f"Task started in background (ID: {task_id})."
    else:
        return await _run()


async def _retry_tool(tool_func, params):
    """Retry wrapper that avoids stale closure references."""
    try:
        success, result = await asyncio.wait_for(
            asyncio.to_thread(tool_func, params),
            timeout=TOOL_TIMEOUT
        )
        return success, result
    except Exception as e:
        return False, f"Retry failed: {e}"


def _store_episode(intent: str, params: dict, result: str, success: bool):
    """Store a completed task in ChromaDB episodic memory."""
    try:
        store = get_memory_store()
        if store.is_ready:
            from executor.window_context import get_active_window_title
            window = get_active_window_title()
            store.store_episode(
                intent=intent,
                params=params,
                result="success" if success else f"failed: {result}",
                response_text=result if isinstance(result, str) else str(result),
                active_window=window,
            )
    except Exception:
        pass  # Memory store failures are non-critical
