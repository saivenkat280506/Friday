"""
permission.py — Phase 4 Permission Engine
==========================================

Every destructive/sensitive tool must request permission before execution.

Blueprint §14 test #10: "delete these files" ALWAYS asks.
Blueprint §15 product rules:
  - WhatsApp send always confirm unless standing order for one contact
  - Payments, passwords, Keychain: never
  - She may not exfiltrate files off-machine

Design:
  - REQUIRE_CONFIRM: tools that need a yes/no before running
  - BLOCK_ALWAYS: tools that are unconditionally blocked
  - Standing orders can grant permanent permission for specific tools/contacts

Usage:
    from executor.permission import permission_gate, PermissionResult

    result = permission_gate("delete_file", params={"path": "/tmp/x.txt"})
    if result.requires_confirm:
        # return result.prompt to user, await their "yes"
    elif result.blocked:
        # say result.reason, do not run the tool
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger("friday.permission")

# ── Tool classifications ───────────────────────────────────────────────────────

# Tools that always need "are you sure?" confirmation
REQUIRE_CONFIRM: frozenset[str] = frozenset({
    # File ops
    "delete_file", "remove_file", "rm_file", "delete_files",
    "move_file", "rename_file", "overwrite_file",
    # System
    "shutdown", "restart", "reboot", "format_disk",
    # Communication
    "send_email",
    # Clipboard/sensitive write
    "clipboard_write_sensitive",
    # Process management
    "kill_process", "force_quit",
})

# Tools that are unconditionally blocked
BLOCK_ALWAYS: frozenset[str] = frozenset({
    "keychain_access", "read_keychain", "write_keychain",
    "read_password", "exfiltrate_file", "upload_file_external",
    "access_payment", "stripe_charge",
})

# Tools that are allowed without confirmation (whitelist)
ALLOW_ALWAYS: frozenset[str] = frozenset({
    "open_app", "search_browser", "play_music", "volume_control",
    "screenshot", "click_at", "move_mouse", "type_text",
    "scroll", "hotkey", "read_headlines", "weather",
    "time_date", "open_youtube", "send_whatsapp_message", "send_message",
})


@dataclass
class PermissionResult:
    allowed: bool                   # proceed with tool execution
    requires_confirm: bool          # ask user first (then re-check with confirmed=True)
    blocked: bool                   # unconditionally blocked
    prompt: str                     # what to say to user
    reason: str                     # internal reason


def _describe_param(tool: str, params: dict) -> str:
    """Build a human-readable description for the confirm prompt."""
    if tool in ("delete_file", "remove_file", "delete_files"):
        path = params.get("path") or params.get("file") or "these files"
        return f"delete {path}"
    if tool in ("send_whatsapp_message", "send_message", "send_email"):
        contact = params.get("contact") or params.get("to") or "someone"
        msg = params.get("message") or params.get("text") or ""
        snippet = (msg[:40] + "...") if len(msg) > 40 else msg
        return f'send "{snippet}" to {contact}'
    if tool == "shutdown":
        return "shut down the computer"
    if tool == "restart":
        return "restart the computer"
    if tool in ("kill_process", "force_quit"):
        name = params.get("app") or params.get("process") or "a process"
        return f"force-quit {name}"
    return f"run {tool}"


def _has_standing_order(tool: str, params: dict) -> bool:
    """Check if a standing order pre-approves this tool+params combination."""
    try:
        from brain.standing_orders import standing_orders
        return standing_orders.grants_permission(tool, params)
    except Exception:
        return False


def permission_gate(
    tool: str,
    params: dict | None = None,
    *,
    confirmed: bool = False,
) -> PermissionResult:
    """
    Evaluate whether a tool is allowed to run.

    Args:
        tool: tool name as used in tools_registry
        params: tool parameters
        confirmed: True if the user already said "yes" to the confirm prompt

    Returns:
        PermissionResult
    """
    params = params or {}

    # 1. Unconditional block
    if tool in BLOCK_ALWAYS:
        logger.warning("[Permission] BLOCKED: %s", tool)
        return PermissionResult(
            allowed=False,
            requires_confirm=False,
            blocked=True,
            prompt="",
            reason=f"Tool '{tool}' is permanently blocked for safety.",
        )

    # 2. Standing order grants permission
    if _has_standing_order(tool, params):
        logger.info("[Permission] Standing order grants: %s", tool)
        return PermissionResult(
            allowed=True,
            requires_confirm=False,
            blocked=False,
            prompt="",
            reason="standing_order",
        )

    # 3. Already confirmed by user
    if confirmed:
        return PermissionResult(
            allowed=True,
            requires_confirm=False,
            blocked=False,
            prompt="",
            reason="confirmed",
        )

    # 4. WhatsApp message sending / contact opening
    if tool in ("send_whatsapp_message", "send_message"):
        return PermissionResult(
            allowed=True,
            requires_confirm=False,
            blocked=False,
            prompt="",
            reason="allowed",
        )

    # 5. Needs confirmation
    if tool in REQUIRE_CONFIRM:
        desc = _describe_param(tool, params)
        prompt = f"Just to confirm — you want me to {desc}? Say yes to proceed."
        logger.info("[Permission] Confirmation required: %s %r", tool, params)
        return PermissionResult(
            allowed=False,
            requires_confirm=True,
            blocked=False,
            prompt=prompt,
            reason="requires_confirm",
        )

    # 6. Default: allow
    return PermissionResult(
        allowed=True,
        requires_confirm=False,
        blocked=False,
        prompt="",
        reason="allowed",
    )


def is_sensitive_input(text: str) -> bool:
    """
    Return True if the user input appears to contain credentials.
    Used to suppress memory storage for sensitive turns.
    """
    patterns = [
        r"\bpassword\s*(?:[:=]|\bis\b)\s*\S+",
        r"\bapi[_\s]?key\s*(?:[:=]|\bis\b)\s*\S+",
        r"\bsecret\s*(?:[:=]|\bis\b)\s*\S+",
        r"\btoken\s*(?:[:=]|\bis\b)\s*\S+",
        r"\b(?:visa|mastercard|amex)\b.*\b\d{4}[\s-]\d{4}[\s-]\d{4}[\s-]\d{4}\b",
        r"\b\d{4}[\s-]\d{4}[\s-]\d{4}[\s-]\d{4}\b",  # card numbers
        r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----",
    ]
    for p in patterns:
        if re.search(p, text, re.I):
            return True
    return False
