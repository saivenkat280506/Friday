"""
whatsapp_phonebook.py — Keyword → phone lookup for reliable WhatsApp sidebar search.

v2: supports case-insensitive alias matching so "sathish", "Sathish", "SATHISH"
all resolve to the same phone number.  Search strategy: phone-number first
(WhatsApp sidebar handles digits reliably), with the display name appended as
a last-resort fallback.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from paths import DATA_DIR

logger = logging.getLogger("friday.whatsapp.phonebook")

PHONEBOOK_PATH = Path(DATA_DIR) / "whatsapp_phonebook.json"

# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

_phonebook_cache: dict | None = None


def _invalidate_cache() -> None:
    global _phonebook_cache
    _phonebook_cache = None


def load_phonebook() -> dict:
    """Load contacts dict from whatsapp_phonebook.json (cached)."""
    global _phonebook_cache
    if _phonebook_cache is not None:
        return _phonebook_cache
    try:
        with PHONEBOOK_PATH.open(encoding="utf-8") as handle:
            payload = json.load(handle)
        _phonebook_cache = payload.get("contacts", {})
        return _phonebook_cache
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not load phonebook at %s: %s", PHONEBOOK_PATH, exc)
        _phonebook_cache = {}
        return _phonebook_cache


def reload_phonebook() -> dict:
    """Force-reload the phonebook (call after adding contacts at runtime)."""
    _invalidate_cache()
    return load_phonebook()


# ---------------------------------------------------------------------------
# Alias-aware lookup
# ---------------------------------------------------------------------------


def _find_entry_by_keyword(keyword: str) -> tuple[str, dict | str | None]:
    """
    Find a phonebook entry matching *keyword* using multiple strategies:

    1. Exact primary-key match  (keys are stored lowercase)
    2. Case-insensitive primary-key match
    3. Alias list scan (case-insensitive, substring-tolerant)
    4. Fuzzy / partial match on primary key or display_name

    Returns (primary_key, entry_value) or ("", None) on miss.
    """
    book = load_phonebook()
    key_lower = keyword.lower().strip()

    # --- 1. Exact key match (fastest) ---
    if key_lower in book:
        return key_lower, book[key_lower]

    # --- 2. Case-insensitive key match ---
    for k, v in book.items():
        if k.lower() == key_lower:
            return k, v

    # --- 3. Alias list match (case-insensitive) ---
    for k, v in book.items():
        if not isinstance(v, dict):
            continue
        aliases: list[str] = v.get("aliases", [])
        for alias in aliases:
            if alias.lower().strip() == key_lower:
                return k, v

    # --- 4. Partial / fuzzy match on display_name or key ---
    # e.g. keyword="sathish kumar" should match key="sathish"
    for k, v in book.items():
        # keyword contains the key  ("sathish kumar" ⊇ "sathish")
        if k.lower() in key_lower or key_lower in k.lower():
            return k, v
        if isinstance(v, dict):
            display = v.get("display_name", "").lower()
            if display and (display in key_lower or key_lower in display):
                return k, v
            # Check aliases for partial match
            for alias in v.get("aliases", []):
                alias_lower = alias.lower().strip()
                if alias_lower in key_lower or key_lower in alias_lower:
                    return k, v

    return "", None


# ---------------------------------------------------------------------------
# Phone-number normalisation
# ---------------------------------------------------------------------------


def _normalize_phone_queries(phone: str) -> list[str]:
    """Build search strings WhatsApp sidebar accepts (ordered best-first).

    Strategy: phone numbers are far more reliable than names in WhatsApp's
    sidebar search.  We generate multiple formats (with/without country code,
    grouped digits, raw digits) so at least one hits.
    """
    raw = phone.strip()
    digits = re.sub(r"\D", "", raw)
    queries: list[str] = []

    # Full international format as typed (e.g. "+91 85199 29108")
    if raw:
        queries.append(raw)

    if digits:
        # Pure digits
        queries.append(digits)

        # Indian numbers: +91XXXXXXXXXX
        if digits.startswith("91") and len(digits) >= 12:
            queries.append(f"+{digits}")
            queries.append(digits[2:])  # without country code

        if len(digits) >= 10:
            last10 = digits[-10:]
            queries.append(last10)
            # Common Indian formatting: 5-digit space 5-digit
            queries.append(f"{last10[:5]} {last10[5:]}")

    # De-duplicate while preserving order
    seen: set[str] = set()
    ordered: list[str] = []
    for q in queries:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            ordered.append(q)
    return ordered


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_contact_keyword(keyword: str) -> tuple[str, list[str], bool]:
    """
    Resolve a voice keyword to search queries.

    The search queries are **phone-number based** when the keyword resolves
    to a phonebook entry.  The display-name / keyword text is appended at the
    end only as a last-resort fallback.

    Returns:
        (canonical_display_name, search_queries_best_first, from_phonebook)
    """
    primary_key, entry = _find_entry_by_keyword(keyword)

    if entry is None:
        # No phonebook match — fall back to raw keyword as search text
        return keyword, [keyword], False

    if isinstance(entry, str):
        phone = entry
        display = keyword
    else:
        phone = entry.get("phone", "")
        display = entry.get("display_name", keyword)

    queries = _normalize_phone_queries(phone)

    # Append display name / raw keyword as last-ditch fallback only
    if keyword not in queries:
        queries.append(keyword)

    logger.info(
        "Phonebook resolved %r → display=%r, queries=%s",
        keyword,
        display,
        queries,
    )
    return display, queries, True


def match_needles(keyword: str) -> list[str]:
    """Needles for matching search result rows and chat headers.

    Includes the phone digits, all known aliases, and the raw keyword
    so that _row_matches_needles / _verify_contact_opened can confidently
    match whichever name WhatsApp displays.
    """
    _, queries, from_book = resolve_contact_keyword(keyword)

    needles: list[str] = [keyword.lower()]

    # Add all query variants
    for q in queries:
        needles.append(q.lower())
        digits = re.sub(r"\D", "", q)
        if len(digits) >= 6:
            needles.append(digits)
            if len(digits) >= 10:
                needles.append(digits[-10:])

    # Add all aliases from phonebook entry so header verification works
    # even when WhatsApp shows a slightly different display name
    _, entry = _find_entry_by_keyword(keyword)
    if isinstance(entry, dict):
        for alias in entry.get("aliases", []):
            needles.append(alias.lower().strip())
        dn = entry.get("display_name", "")
        if dn:
            needles.append(dn.lower().strip())

    # De-duplicate preserving order
    seen: set[str] = set()
    out: list[str] = []
    for n in needles:
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out