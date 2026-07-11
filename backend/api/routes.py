"""
routes.py — FastAPI HTTP and WebSocket endpoints.

Grouped by concern: health/settings, chat/voice, vision, agent, pipeline.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import tempfile
import time
import uuid

from fastapi import FastAPI, File, Form, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from brain.friday_graph import run_pipeline
from brain.memory import MEMORY_FILE, _lock
from brain.memory_manager import MemoryManager

from services.command_processor import process_command, process_command_with_timeout
from services.event_bus import BusEvent, event_bus
from services.runtime_state import (
    flags,
    register_session,
    reset_processing_state,
    set_state,
    state_lock,
    stop_event,
    SystemState,
    unregister_sessions_for_thread,
)
from services.vision_service import vision_agent
from services.websocket_manager import ws_manager

logger = logging.getLogger("friday.api")

_pipeline_memory = MemoryManager()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class VisionPayload(BaseModel):
    description: str = ""
    image_base64: str = ""


class PipelineRunRequest(BaseModel):
    text: str
    session_id: str = "default"
    llm_provider: str = "groq"
    llm_model: str = "llama-3.1-8b-instant"


class PipelineRunResponse(BaseModel):
    session_id: str
    intent: str
    final_response: str
    tts_text: str
    ui_event: dict | None
    execution_status: str
    tool_calls_count: int
    elapsed_ms: float


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------


def register_routes(app: FastAPI) -> None:
    """Attach all HTTP and WebSocket routes to the FastAPI application."""

    # ── Health & settings ─────────────────────────────────────────────────

    @app.get("/health")
    async def health():
        return {"status": "online"}

    @app.get("/settings")
    async def get_settings_endpoint():
        from brain.settings import get_settings as gs

        return gs()

    @app.post("/settings")
    async def save_settings_endpoint(payload: dict):
        from brain.settings import update_settings

        return update_settings(payload)

    @app.post("/toggle-mute")
    async def toggle_mute_endpoint():
        from brain.settings import toggle_mute as tm

        return {"muted": tm()}

    # ── Chat & voice triggers ─────────────────────────────────────────────

    @app.post("/chat")
    async def chat_endpoint(payload: dict):
        flags.continuous_voice_mode = False
        from tts.pocket_tts import stop_speech

        stop_speech()
        text = payload.get("text", "")
        voice = payload.get("voice", False)
        request_id = payload.get("id")
        if request_id:
            register_session(request_id)
        return StreamingResponse(
            process_command_with_timeout(text, request_id, voice=voice),
            media_type="text/event-stream",
        )

    @app.post("/listen-trigger")
    async def listen_trigger():
        flags.force_listen_trigger = True
        event_bus.emit_nowait(BusEvent("wake"))
        return {"status": "triggered"}

    @app.post("/stop-trigger")
    async def stop_trigger():
        reset_processing_state()
        from tts.pocket_tts import stop_speech

        stop_speech()
        event_bus.emit_nowait(BusEvent("stop"))
        logger.info("Stop trigger received")
        return {"status": "stopping"}

    @app.post("/voice")
    async def voice_endpoint(audio: UploadFile = File(...), id: str = Form(None)):
        with state_lock:
            if flags.is_listening:
                logger.info("Voice upload blocked — already listening")
                return StreamingResponse(
                    iter(['data: {"error": "Already listening", "done": true}\n\n']),
                    media_type="text/event-stream",
                )
            flags.is_listening = True

        text = ""
        request_id = id
        try:
            if request_id:
                register_session(request_id)
            temp_path = os.path.join(tempfile.gettempdir(), f"temp_{audio.filename}")
            with open(temp_path, "wb") as handle:
                handle.write(await audio.read())

            try:
                from stt.stt import _get_groq_client

                with open(temp_path, "rb") as audio_file:
                    response = _get_groq_client().audio.transcriptions.create(
                        file=(audio.filename, audio_file.read()),
                        model="whisper-large-v3",
                        language="en",
                        prompt="F.R.I.D.A.Y., Friday, WhatsApp, Chrome, Laxman, Vaasavi, aka, message.",
                    )
                text = response.text.strip()
                await ws_manager.broadcast_json({"type": "transcript", "text": text})
                await ws_manager.broadcast_json({"type": "user_message", "text": text})
            except Exception as exc:
                logger.error("Cloud transcription failed: %s", exc)
                text = ""
            finally:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
        finally:
            with state_lock:
                flags.is_listening = False

        if not text:
            payload_err = json.dumps({"error": "Could not transcribe audio", "done": True})
            return StreamingResponse(
                iter([f"data: {payload_err}\n\n"]),
                media_type="text/event-stream",
            )

        return StreamingResponse(
            process_command(text, request_id, voice=True),
            media_type="text/event-stream",
        )

    # ── WebSocket (event bus bridge) ──────────────────────────────────────

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        """
        Primary WebSocket bridge.

        Client events are translated to BusEvent objects; the voice loop
        and command processor handle them asynchronously.
        """
        await ws_manager.connect(websocket)
        ws_thread_id = str(uuid.uuid4())
        logger.info("WebSocket connected, thread_id=%s", ws_thread_id)
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                event_type = payload.get("event") or payload.get("type", "")
                if event_type == "command":
                    req_id = payload.get("id") or str(uuid.uuid4())
                    register_session(req_id, ws_thread_id)
                    event_bus.emit_nowait(BusEvent("command", {
                        "text": payload.get("text", ""),
                        "id": req_id,
                        "voice": payload.get("voice", False),
                    }))
                elif event_type == "wake":
                    event_bus.emit_nowait(BusEvent("wake"))
                elif event_type == "stop":
                    event_bus.emit_nowait(BusEvent("stop"))
                elif event_type == "mute":
                    event_bus.emit_nowait(BusEvent("mute", {"muted": payload.get("muted", True)}))
                else:
                    logger.debug("Unknown WebSocket event: %s", event_type)
        except WebSocketDisconnect:
            ws_manager.disconnect(websocket)
            cleaned = unregister_sessions_for_thread(ws_thread_id)
            logger.info("WebSocket closed, cleaned %d session mappings", cleaned)

    # ── Reset ─────────────────────────────────────────────────────────────

    @app.post("/reset")
    async def reset_endpoint():
        logger.info("Reset requested — clearing state and background tasks")
        try:
            from tts.pocket_tts import stop_speech

            stop_speech()
        except Exception as exc:
            logger.warning("Error stopping speech during reset: %s", exc)

        reset_processing_state()

        try:
            vision_agent.last_desc = ""
            vision_agent.action_history.clear()
            while not vision_agent.queue.empty():
                try:
                    vision_agent.queue.get_nowait()
                    vision_agent.queue.task_done()
                except asyncio.QueueEmpty:
                    break
        except Exception as exc:
            logger.warning("Error clearing vision agent: %s", exc)

        task_count = 0
        try:
            from executor.task_manager import task_manager

            for tid in list(task_manager.active_tasks.keys()):
                if task_manager.cancel_task(tid):
                    task_count += 1
        except Exception as exc:
            logger.warning("Error cancelling background tasks: %s", exc)

        try:
            mem = {"history": [], "last_contact": None, "last_song": None}
            with _lock:
                with open(MEMORY_FILE, "w", encoding="utf-8") as handle:
                    json.dump(mem, handle)
        except Exception as exc:
            logger.warning("Error clearing short-term memory: %s", exc)

        try:
            from paths import CHECKPOINTS_DB as db_path
            if os.path.exists(db_path):
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()
                cursor.execute("DELETE FROM checkpoints")
                cursor.execute("DELETE FROM writes")
                conn.commit()
                conn.close()
        except Exception as exc:
            logger.warning("Error clearing checkpoints database: %s", exc)

        try:
            os.system("taskkill /f /im WhatsApp.exe")
        except Exception as exc:
            logger.warning("WhatsApp taskkill error: %s", exc)

        await set_state(SystemState.IDLE)
        await ws_manager.broadcast_json({"type": "reset_complete"})
        logger.info("Reset complete — stopped %d background tasks", task_count)
        return {"status": "ok", "cancelled_tasks": task_count}

    # ── Vision ────────────────────────────────────────────────────────────

    @app.post("/agent/vision")
    async def vision_callback(payload: VisionPayload):
        if payload.image_base64:
            try:
                from vision.vision_analyzer import analyze_screen

                desc = await asyncio.to_thread(analyze_screen, payload.image_base64)
                logger.debug("Vision image analyzed: %s…", desc[:80])
                vision_agent.last_desc = desc
                return {"status": "analyzed", "description": desc}
            except Exception as exc:
                logger.error("Vision analysis error: %s", exc)
                return {"status": "error", "message": str(exc)}
        if payload.description:
            logger.debug("Screen description received: %s…", payload.description[:60])
            await vision_agent.enqueue_vision(payload.description)
            return {"status": "received", "description": payload.description}
        return {"status": "no_data"}

    @app.get("/agent/vision/capture")
    async def capture_and_analyze():
        try:
            from vision.capture import capture_screen_base64
            from vision.vision_analyzer import analyze_screen

            img = await asyncio.to_thread(capture_screen_base64, False)
            desc = await asyncio.to_thread(analyze_screen, img)
            vision_agent.last_desc = desc
            return {"status": "ok", "description": desc}
        except Exception as exc:
            logger.error("Screen capture/analysis failed: %s", exc)
            return {"status": "error", "message": str(exc)}

    # ── Autonomous agent ──────────────────────────────────────────────────

    @app.post("/agent/run")
    async def agent_run_endpoint(request: dict):
        task = request.get("task", "").strip()
        if not task:
            return {"error": "task is required"}

        from executor.web_agent import run_web_agent_streaming

        async def _stream():
            async for chunk in run_web_agent_streaming(
                task=task,
                broadcast_fn=ws_manager.broadcast_json,
                max_steps=15,
                use_vision=False,
            ):
                yield chunk

        return StreamingResponse(_stream(), media_type="text/event-stream")

    @app.post("/agent/stop")
    async def agent_stop_endpoint():
        from executor.web_agent import request_stop

        request_stop()
        await ws_manager.broadcast_json({
            "type": "agent_step",
            "step": 0,
            "action": "STOPPED",
            "result": "Stop requested by user.",
            "status": "stopped",
        })
        return {"status": "stop_requested"}

    # ── Pipeline API ──────────────────────────────────────────────────────

    @app.post("/pipeline/run")
    async def run_pipeline_endpoint(req: PipelineRunRequest):
        t0 = time.perf_counter()
        state = await run_pipeline(
            raw_input=req.text,
            session_id=req.session_id,
            llm_provider=req.llm_provider,
            llm_model=req.llm_model,
        )
        elapsed = (time.perf_counter() - t0) * 1000
        return PipelineRunResponse(
            session_id=req.session_id,
            intent=state["intent"].value if hasattr(state["intent"], "value") else str(state["intent"]),
            final_response=state["final_response"],
            tts_text=state["tts_text"],
            ui_event=state.get("ui_event"),
            execution_status=str(state.get("execution_status", "unknown")),
            tool_calls_count=len(state.get("tool_calls", [])),
            elapsed_ms=round(elapsed, 1),
        )

    @app.get("/pipeline/status")
    async def pipeline_status():
        from executor.browser_agent_client import is_browser_agent_available

        browser_ok = await is_browser_agent_available()
        return {
            "status": "ok",
            "llm_provider": "groq",
            "browser_agent_online": browser_ok,
            "active_sessions": 0,
        }

    @app.websocket("/pipeline/ws/{session_id}")
    async def websocket_pipeline(websocket: WebSocket, session_id: str):
        await websocket.accept()
        logger.info("Pipeline WS connected: %s", session_id)
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.send_json({"type": "ERROR", "message": "Invalid JSON"})
                    continue

                msg_type = msg.get("type", "TEXT")
                if msg_type == "PING":
                    await websocket.send_json({"type": "PONG"})
                    continue
                if msg_type == "RESET":
                    _pipeline_memory.forget_session(session_id)
                    await websocket.send_json({"type": "RESET_OK"})
                    continue
                if msg_type == "TEXT":
                    text = msg.get("text", "").strip()
                    if not text:
                        continue
                    await websocket.send_json({"type": "THINKING"})
                    state = await run_pipeline(
                        raw_input=text,
                        session_id=session_id,
                        llm_provider=msg.get("llm_provider", "groq"),
                        llm_model=msg.get("llm_model", "llama-3.1-8b-instant"),
                    )
                    await websocket.send_json({
                        "type": "RESPONSE",
                        "intent": (
                            state["intent"].value
                            if hasattr(state["intent"], "value")
                            else str(state["intent"])
                        ),
                        "text": state["final_response"],
                        "tts": state["tts_text"],
                        "status": str(state.get("execution_status", "unknown")),
                        "tools": [c["tool_name"] for c in state.get("tool_calls", [])],
                    })
                    if state.get("ui_event"):
                        await websocket.send_json({"type": "UI_EVENT", **state["ui_event"]})
        except WebSocketDisconnect:
            logger.info("Pipeline WS disconnected: %s", session_id)
        except Exception as exc:
            logger.exception("Pipeline WS error [%s]: %s", session_id, exc)
            try:
                await websocket.send_json({"type": "ERROR", "message": str(exc)})
            except Exception:
                pass

    @app.get("/pipeline/memory/{session_id}")
    async def get_pipeline_memory(session_id: str):
        return {
            "session_id": session_id,
            "short_term": _pipeline_memory.get_short_term(session_id),
            "preferences": _pipeline_memory.get_preferences(session_id),
        }

    @app.delete("/pipeline/memory/{session_id}")
    async def clear_pipeline_memory(session_id: str):
        _pipeline_memory.forget_session(session_id)
        return {"status": "cleared", "session_id": session_id}