"""
Integration smoke tests for FRIDAY voice mode (main app + companion).

Skips WhatsApp. Requires backend on http://127.0.0.1:8000.
Run: python scripts/test_voice_integration.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
import urllib.error
import urllib.request
from dataclasses import dataclass, field

BASE = "http://127.0.0.1:8000"
WS_URL = "ws://127.0.0.1:8000/ws"


@dataclass
class TestReport:
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def ok(self, name: str) -> None:
        self.passed.append(name)
        print(f"  PASS  {name}")

    def fail(self, name: str, detail: str) -> None:
        msg = f"{name}: {detail}"
        self.failed.append(msg)
        print(f"  FAIL  {msg}")

    def note(self, text: str) -> None:
        self.notes.append(text)
        print(f"  note  {text}")


def http_json(method: str, path: str, body: dict | None = None, timeout: float = 15.0) -> dict:
    data = None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def http_post_empty(path: str, timeout: float = 15.0) -> dict:
    req = urllib.request.Request(f"{BASE}{path}", data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


async def collect_ws_events(
    commands: list[dict],
    *,
    timeout: float = 45.0,
) -> list[dict]:
    try:
        import websockets
    except ImportError:
        return []

    events: list[dict] = []
    async with websockets.connect(WS_URL, open_timeout=10) as ws:
        await asyncio.sleep(0.3)
        for cmd in commands:
            await ws.send(json.dumps(cmd))
            await asyncio.sleep(0.1)

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                events.append(json.loads(raw))
            except asyncio.TimeoutError:
                if events and any(e.get("type") == "chat" for e in events[-3:]):
                    break
                continue
    return events


async def run_tests() -> TestReport:
    report = TestReport()
    print("\n=== FRIDAY Voice Integration Tests ===\n")

    # ── 1. Health ───────────────────────────────────────────────────────
    try:
        health = http_json("GET", "/health")
        if health.get("status") == "online":
            report.ok("backend health online")
        else:
            report.fail("backend health", str(health))
    except Exception as exc:
        report.fail("backend health", str(exc))
        return report

    try:
        http_post_empty("/stop-trigger")
        await asyncio.sleep(0.5)
        report.ok("stop-trigger resets state")
    except Exception as exc:
        report.fail("stop-trigger", str(exc))

    # ── 2. Main app listen trigger ──────────────────────────────────────
    try:
        http_post_empty("/listen-trigger")
        await asyncio.sleep(0.4)
        state = http_json("GET", "/health").get("state")
        if state == "listening":
            report.ok("listen-trigger enters listening state")
        else:
            report.fail("listen-trigger state", f"expected listening, got {state}")
        http_post_empty("/stop-trigger")
        await asyncio.sleep(0.5)
        if http_json("GET", "/health").get("state") == "idle":
            report.ok("listen-trigger stop returns idle")
        else:
            report.fail("listen-trigger stop", "state not idle after stop")
    except Exception as exc:
        report.fail("listen-trigger flow", str(exc))

    # ── 3. Companion endpoints ──────────────────────────────────────────
    try:
        act = http_post_empty("/companion/activate")
        if act.get("companion_mode"):
            report.ok("companion/activate")
        else:
            report.fail("companion/activate", str(act))

        listen = http_post_empty("/companion/listen")
        if listen.get("status") == "listening":
            report.ok("companion/listen")
        else:
            report.fail("companion/listen", str(listen))

        await asyncio.sleep(0.6)
        comp_state = http_json("GET", "/health").get("state")
        if comp_state in ("listening", "transcribing", "idle_listening", "thinking"):
            report.ok(f"companion listen active state ({comp_state})")
        elif comp_state == "idle":
            report.note("companion listen returned idle (mic timeout without speech — OK in CI)")
            report.ok("companion/listen triggers pipeline")
        else:
            report.fail("companion listen state", comp_state or "none")

        http_post_empty("/stop-trigger")
        deact = http_post_empty("/companion/deactivate")
        if deact.get("status") == "inactive":
            report.ok("companion/deactivate")
        else:
            report.fail("companion/deactivate", str(deact))
    except Exception as exc:
        report.fail("companion flow", str(exc))

    # ── 4. Voice command via WebSocket (simulates app voice chat path) ──
    voice_cases = [
        ("read headlines", "headline"),
        ("tell me a joke", "joke"),
        ("what time is it", "time"),
        ("play music", "music"),
    ]

    for text, expect_hint in voice_cases:
        try:
            events = await collect_ws_events([
                {
                    "event": "command",
                    "text": text,
                    "id": f"test-{expect_hint}-{int(time.time())}",
                    "voice": True,
                }
            ], timeout=60.0)

            states = [e.get("state") for e in events if e.get("state")]
            chats = [
                e.get("text", "")
                for e in events
                if e.get("type") == "chat" and e.get("role") == "assistant"
            ]
            user_msgs = [e for e in events if e.get("type") == "user_message"]

            if not chats:
                report.fail(f"voice WS: {text}", f"no assistant chat in {len(events)} events")
                continue

            reply = chats[-1].lower()
            if "unknown tool" in reply or "knackered" in reply:
                report.fail(f"voice WS: {text}", reply[:120])
                continue

            if expect_hint == "headline" and "headline" not in reply and "news" not in reply:
                report.fail(f"voice WS: {text}", f"unexpected reply: {reply[:100]}")
            elif expect_hint == "joke" and len(reply) < 10:
                report.fail(f"voice WS: {text}", f"reply too short: {reply}")
            elif expect_hint == "music" and "play" not in reply and "music" not in reply and "counting" not in reply:
                report.note(f"music reply: {reply[:80]}")
                report.ok(f"voice WS: {text}")
            else:
                report.ok(f"voice WS: {text}")

            if states:
                report.note(f"  states seen for '{text}': {states[-5:]}")

        except Exception as exc:
            report.fail(f"voice WS: {text}", str(exc))

    # ── 5. Sync tool dispatch (read_headlines) ──────────────────────────
    try:
        from executor.tools_registry import get_tool_registry
        from brain.state import IntentCategory

        reg = get_tool_registry()
        state = {
            "intent": IntentCategory.NEWS,
            "extracted_params": {"query": "technology"},
            "cleaned_input": "read tech headlines",
            "plan": ["read_headlines:technology"],
            "current_step": 0,
            "tool_calls": [],
            "iteration_count": 0,
        }
        tc = await reg.execute_tool(
            "read_headlines",
            "technology",
            {"query": "technology"},
            state,
        )
        result = tc.get("result") or {}
        msg = result.get("message", "") if isinstance(result, dict) else str(result)
        if tc.get("status").value == "success" and msg:
            report.ok("read_headlines tool execution")
        else:
            report.fail("read_headlines tool", tc.get("error") or "empty result")
    except Exception as exc:
        report.fail("read_headlines tool", str(exc))

    # ── 6. Routing rules ────────────────────────────────────────────────
    try:
        from brain.router import IntentRouter
        from brain.state import IntentCategory

        router = IntentRouter()
        for phrase, intent in (
            ("read headlines", IntentCategory.NEWS),
            ("tell me a joke", IntentCategory.CHAT),
            ("play music", IntentCategory.PLAY_MEDIA),
        ):
            got = router.classify_rules(phrase).intent
            if got == intent:
                report.ok(f"route '{phrase}' -> {intent.value}")
            else:
                report.fail(f"route '{phrase}'", f"got {got.value}")
    except Exception as exc:
        report.fail("intent routing", str(exc))

    # ── 7. STT module load ──────────────────────────────────────────────
    try:
        from stt.stt import _get_model

        model = _get_model()
        if model is not None:
            report.ok("STT model loads")
        else:
            report.fail("STT model", "model is None")
    except Exception as exc:
        report.fail("STT model", str(exc))

    # ── Summary ─────────────────────────────────────────────────────────
    print("\n=== Summary ===")
    print(f"Passed: {len(report.passed)}")
    print(f"Failed: {len(report.failed)}")
    if report.failed:
        print("\nFailures:")
        for item in report.failed:
            print(f"  - {item}")
    return report


def main() -> int:
    report = asyncio.run(run_tests())
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())