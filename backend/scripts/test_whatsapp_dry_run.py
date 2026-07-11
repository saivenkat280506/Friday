"""
Dry-run WhatsApp automation test — open, search, verify, focus message box.
Does NOT send messages when WHATSAPP_DRY_RUN=1 (default for this script).

Usage:
  set WHATSAPP_DRY_RUN=1
  set WHATSAPP_FAST=1
  python scripts/test_whatsapp_dry_run.py [contact_name]
"""

from __future__ import annotations

import os
import sys
import time

# Ensure backend package root is on path
_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

os.environ.setdefault("WHATSAPP_DRY_RUN", "1")
os.environ.setdefault("WHATSAPP_FAST", "1")


def main() -> int:
    contact = sys.argv[1] if len(sys.argv) > 1 else "Laxman"
    test_message = "FRIDAY dry-run test — this must NOT be sent"

    print("=" * 60)
    print("FRIDAY WhatsApp dry-run test")
    print(f"  DRY_RUN={os.environ.get('WHATSAPP_DRY_RUN')}")
    print(f"  FAST={os.environ.get('WHATSAPP_FAST')}")
    print(f"  contact={contact!r}")
    print("=" * 60)

    # Re-import after env is set so module flags pick up DRY_RUN/FAST_MODE
    import importlib
    import executor.whatsapp_handler as wa

    importlib.reload(wa)
    from executor.automation import open_whatsapp

    # ── Test 1: Open only (skipped if already running) ─────────────────
    from executor.whatsapp_handler import _whatsapp_already_open

    if _whatsapp_already_open():
        print("\n[1] open_whatsapp: skipped (already running)")
    else:
        t0 = time.perf_counter()
        ok, status = open_whatsapp()
        t_open = time.perf_counter() - t0
        print(f"\n[1] open_whatsapp: ok={ok} ({t_open:.1f}s)")
        print(f"    status: {status}")
        if not ok:
            return 1

    # ── Test 2: Full dry-run pipeline (search + verify + focus, no send) ──
    t0 = time.perf_counter()
    result = wa.send_whatsapp_message_sync(contact, test_message)
    t_flow = time.perf_counter() - t0

    print(f"\n[2] send_whatsapp_message (dry-run): ({t_flow:.1f}s)")
    for key in ("success", "stage", "dry_run", "contact", "error"):
        if key in result:
            print(f"    {key}: {result[key]}")

    if result.get("dry_run") and result.get("stage") == "dry_run_complete":
        print("\n✓ Dry-run passed — opened, searched, verified, focused message box.")
        print("  No message was sent.")
        return 0

    if result.get("success"):
        print("\n✓ Flow completed (search-only or partial success).")
        return 0

    print("\n✗ Dry-run failed.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())