"""
redact.py — Phase 5 Secret Redactor
======================================

Strips credentials and sensitive data before memory storage.

Blueprint §15: "Payments, passwords, Keychain: never"

Uses regex patterns only — no LLM call, zero latency.

Usage:
    from brain.redact import redact

    safe = redact("My password is hunter2")
    # → "My password is [REDACTED]"

    is_sensitive = contains_secret("api_key=sk-abc123")
    # → True
"""

from __future__ import annotations

import re
from typing import Pattern

# ── Redaction patterns ────────────────────────────────────────────────────────

_PATTERNS: list[tuple[Pattern[str], str]] = [
    # API keys (OpenAI, Groq, Anthropic, etc.)
    (re.compile(r"\b(sk-[a-zA-Z0-9]{20,})\b"), "[API_KEY]"),
    (re.compile(r"\b(gsk_[a-zA-Z0-9]{20,})\b"), "[GROQ_KEY]"),
    (re.compile(r"\b(xoxb-[a-zA-Z0-9\-]{20,})\b"), "[SLACK_TOKEN]"),
    (re.compile(r"\b(ghp_[a-zA-Z0-9]{30,})\b"), "[GITHUB_TOKEN]"),

    # Credit card numbers (Luhn-like patterns)
    (re.compile(r"\b(?:\d{4}[\s\-]){3}\d{4}\b"), "[CARD_NUMBER]"),
    (re.compile(r"\b\d{16}\b"), "[CARD_NUMBER]"),

    # SSN / National ID
    (re.compile(r"\b\d{3}[-\s]\d{2}[-\s]\d{4}\b"), "[SSN]"),

    # Passwords after common keywords
    (re.compile(r"\b(password|passwd|pwd|pass)\s*[:=\s]\s*(\S+)", re.I), r"\1 [REDACTED]"),

    # Generic secret/key= patterns
    (re.compile(r"\b(secret|api[_\s]?key|token|auth)\s*([:=])\s*(\S+)", re.I), r"\1\2[REDACTED]"),

    # Private key blocks
    (re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----.*?-----END (?:RSA |EC )?PRIVATE KEY-----", re.DOTALL), "[PRIVATE_KEY]"),
]

# ── Quick-detect (no substitution) ────────────────────────────────────────────

_DETECT_PATTERNS: list[Pattern[str]] = [
    re.compile(r"\b(sk-|gsk_|xoxb-|ghp_)", re.I),
    re.compile(r"\b(password|passwd|pwd)\s*[:=]\s*(?!\[REDACTED\])", re.I),
    re.compile(r"\b(secret|api[_\s]?key|token)\s*[:=]\s*(?!\[[A-Z_]+\])\S{8,}", re.I),
    re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
    re.compile(r"\b(?:\d{4}[\s\-]){3}\d{4}\b"),
]


def redact(text: str) -> str:
    """
    Remove secrets from text before memory storage.
    Returns the redacted string. Non-destructive to non-secret content.
    """
    if not text:
        return text
    result = text
    for pattern, replacement in _PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def contains_secret(text: str) -> bool:
    """
    Fast check: does this text contain any credential-like patterns?
    Use this to decide whether to skip memory storage entirely.
    """
    if not text:
        return False
    for p in _DETECT_PATTERNS:
        if p.search(text):
            return True
    return False


def safe_for_memory(text: str) -> str | None:
    """
    If text contains secrets, redact it.
    If redaction fails to remove all secrets (pathological case), return None
    so the caller skips memory storage entirely.

    Returns:
        str: safe text (may be redacted)
        None: skip memory storage entirely
    """
    if not contains_secret(text):
        return text
    cleaned = redact(text)
    # Verify no obvious secrets remain
    if contains_secret(cleaned):
        return None  # don't store
    return cleaned
