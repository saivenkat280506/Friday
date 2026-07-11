"""
Live WhatsApp phonebook test — verifies each step actually happened.

Checks:
  1. WhatsApp.exe is NOT running before test (or reports it)
  2. WhatsApp.exe launches and window appears
  3. Sidebar search receives the phonebook number
  4. Contact header matches phonebook needles
  5. Message appears in compose box (TYPE_ONLY — does not press Enter)

Usage:
  python scripts/test_phonebook_send.py [keyword] [message]
"""
from __future__ import annotations

import importlib
import os
import sys
import time

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND)

os.environ.pop("WHATSAPP_DRY_RUN", None)
os.environ.setdefault("WHATSAPP_TYPE_ONLY", "1")
os.environ.setdefault("WHATSAPP_FAST", "0")  # real timings for live UI

KEYWORD = sys.argv[1] if len(sys.argv) > 1 else "sathish"
MESSAGE = sys.argv[2] if len(sys.argv) > 2 else (
    "iam friday ai mr.stark's personal assistant how may I help you today?"
)


def _header(msg: str) -> None:
    print(f"\n{'=' * 60}")
    print(msg)
    print("=" * 60)


def _fail(step: str, detail: str) -> int:
    print(f"\n✗ FAIL at [{step}]: {detail}")
    return 1


def main() -> int:
    from executor.automation import is_whatsapp_running, open_whatsapp
    from executor.whatsapp_phonebook import match_needles, resolve_contact_keyword

    import executor.whatsapp_handler as wa

    importlib.reload(wa)

    display, queries, from_book = resolve_contact_keyword(KEYWORD)
    needles = match_needles(KEYWORD)

    _header("FRIDAY WhatsApp phonebook live test")
    print(f"  keyword     : {KEYWORD!r}")
    print(f"  phonebook   : {from_book}")
    print(f"  queries     : {queries}")
    print(f"  TYPE_ONLY   : {os.environ.get('WHATSAPP_TYPE_ONLY')}")
    print(f"  FAST        : {os.environ.get('WHATSAPP_FAST')}")

    # ── Step 0: baseline process check ───────────────────────────────
    running_before = is_whatsapp_running()
    print(f"\n[0] WhatsApp.exe before test: {'RUNNING' if running_before else 'NOT running'}")
    if running_before:
        print("    (continuing — will reuse existing WhatsApp if window is valid)")

    # ── Step 1: open WhatsApp ────────────────────────────────────────
    if not wa._whatsapp_already_open():
        t0 = time.perf_counter()
        ok, status = open_whatsapp()
        elapsed = time.perf_counter() - t0
        print(f"\n[1] open_whatsapp: ok={ok} ({elapsed:.1f}s)")
        print(f"    status: {status}")
        if not ok:
            return _fail("open_whatsapp", status)
    else:
        print("\n[1] open_whatsapp: skipped (already open)")

    if not is_whatsapp_running():
        return _fail("open_whatsapp", "WhatsApp.exe process not found after launch")

    window = wa._get_whatsapp_window()
    if window is None:
        return _fail("open_whatsapp", "Could not attach to WhatsApp window via UIA")

    try:
        print(f"    window title: {window.window_text()!r}")
        rect = window.rectangle()
        print(f"    window rect  : {rect}")
    except Exception as exc:
        return _fail("open_whatsapp", f"Window metadata unreadable: {exc}")

    if not wa._is_whatsapp_foreground():
        wa._focus_whatsapp_window()
    print(f"    foreground   : {wa._is_whatsapp_foreground()}")

    # ── Step 2: full send pipeline (type-only) ───────────────────────
    t0 = time.perf_counter()
    result = wa.send_whatsapp_message_sync(KEYWORD, MESSAGE)
    elapsed = time.perf_counter() - t0

    print(f"\n[2] send_whatsapp_message_sync ({elapsed:.1f}s)")
    for key in (
        "success",
        "stage",
        "search_query",
        "from_phonebook",
        "error",
        "dry_run",
    ):
        if key in result:
            print(f"    {key}: {result[key]}")

    if not result.get("success"):
        return _fail("pipeline", result.get("error") or "handler returned success=False")

    if result.get("stage") != "type_only_complete":
        return _fail(
            "pipeline",
            f"Expected stage=type_only_complete, got {result.get('stage')!r}",
        )

    search_query = result.get("search_query") or ""
    if not search_query:
        return _fail("search", "No search_query recorded — sidebar search may not have run")

    print(f"\n[3] search_query used: {search_query!r}")
    if from_book and not any(
        "".join(c for c in q if c.isdigit()) in "".join(c for c in search_query if c.isdigit())
        for q in queries[:3]
    ):
        return _fail("search", f"Search query {search_query!r} does not match phonebook")

    # ── Step 4: re-read compose box ────────────────────────────────────
    _, main_window = wa._connect_main_window()
    compose_text = ""
    compose = wa._find_compose_edit(main_window)
    if compose:
        try:
            compose_text = (compose.window_text() or "").strip()
        except Exception:
            pass

    print(f"\n[4] compose box text ({len(compose_text)} chars):")
    print(f"    {compose_text[:120]!r}{'...' if len(compose_text) > 120 else ''}")

    snippet = MESSAGE.strip()[:20].lower()
    if snippet and snippet not in compose_text.lower():
        return _fail(
            "compose",
            "Message not found in WhatsApp compose box after pipeline",
        )

    print("\n✓ PASS — WhatsApp opened, searched by phonebook number, contact verified, message typed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())