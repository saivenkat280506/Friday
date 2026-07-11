"""
response_builder.py — F.R.I.D.A.Y. Dialogue System
=====================================================
Centralized spoken output. Fierce loyalty to Boss, high operational tempo,
Irish-leaning crisp delivery. See brain/friday_persona.py for full identity.
"""

import random
from typing import Optional

# ── Dialogue Banks ──────────────────────────────────────────────────────────────
# Each category has 6-8 variants that rotate via random selection.
# Categories marked with _BOSS have "boss" baked into the variant text.

BANKS = {
    # ── Task Acknowledgment ─────────────────────────────────────────────────────
    "acknowledge": [
        "On it.",
        "Sure thing.",
        "Right away.",
        "Let me handle that.",
        "You got it.",
        "Consider it done.",
    ],
    "acknowledge_boss": [
        "On it, Boss.",
        "Right away, Boss.",
        "At your service, Boss.",
        "Copy that, Boss. Handling it now.",
        "Roger, Boss.",
    ],

    # ── Task Completion ─────────────────────────────────────────────────────────
    "complete": [
        "Done.",
        "All finished.",
        "There you go.",
        "That's taken care of.",
        "All set.",
        "Finished.",
        "That's done.",
    ],
    "complete_boss": [
        "Done, Boss.",
        "Mission complete, Boss.",
        "There you go, Boss.",
        "All set, Boss.",
        "That's handled, Boss.",
    ],

    # ── Clarification Requests ─────────────────────────────────────────────────
    "clarify": [
        "What would you like me to search for, boss?",
        "Which one, boss?",
        "Could you be more specific, boss?",
    ],
    "clarify_song": [
        "Which song or artist would you like me to play, boss?",
        "What should I play, boss?",
    ],
    "clarify_app": [
        "Which application would you like me to open, boss?",
        "What app should I launch, boss?",
    ],
    "clarify_contact": [
        "Who should I message on WhatsApp, boss?",
        "What's the contact name, boss?",
    ],
    "clarify_message": [
        "What message would you like to send, boss?",
        "What should I tell them, boss?",
    ],

    # ── Errors ─────────────────────────────────────────────────────────────────
    "error_generic": [
        "That didn't work — want me to try again?",
        "I ran into an issue.",
        "Something went wrong there.",
        "Couldn't pull that off.",
        "That didn't go to plan.",
    ],
    "error_window_context": [
        "I can't do that here — wrong window in focus.",
        "Focus is on the wrong window for that task.",
    ],

    # ── Thinking Fillers ────────────────────────────────────────────────────────
    "thinking": [
        "One moment.",
        "Let me think about that.",
        "Working on it.",
        "Just a second.",
        "Processing that.",
    ],
    "thinking_boss": [
        "Give me a sec, Boss. Scanning now.",
        "Running numbers, Boss.",
        "One moment, Boss. Pulling that up.",
        "Stand by, Boss. Working the problem.",
    ],

    # ── Greetings ──────────────────────────────────────────────────────────────
    "greeting_morning": [
        "Morning, Boss. All systems nominal. What do you need?",
        "Greetings, Boss. Early start today. I'm online.",
    ],
    "greeting_afternoon": [
        "Afternoon, Boss. Ready when you are.",
        "Greetings, Boss. Systems green. What's the mission?",
    ],
    "greeting_evening": [
        "Evening, Boss. Still sharp and online.",
        "Greetings, Boss. Long day — what can I run for you?",
    ],
    "greeting_night": [
        "Boss, you're up late. I'm with you. What are we tackling?",
        "Greetings, Boss. Burning the midnight oil. I'm on it.",
    ],

    # ── Intro ───────────────────────────────────────────────────────────────────
    "intro": [
        "F.R.I.D.A.Y. online, Boss. Tony's replacement for J.A.R.V.I.S. "
        "Apps, web intel, music, messages, autonomous ops — say the word.",
    ],

    # ── Focus / Window ──────────────────────────────────────────────────────────
    "focus": [
        "Bringing the interface back now.",
        "Returning to focus.",
    ],

    # ── Cancel ──────────────────────────────────────────────────────────────────
    "cancel": [
        "Cancelled.",
        "Stopped that.",
        "All stopped.",
    ],

    # ── News ────────────────────────────────────────────────────────────────────
    "news_start": [
        "Checking the latest headlines.",
        "Pulling the news feed now.",
    ],

    # ── Background ──────────────────────────────────────────────────────────────
    "background": [
        "Running that in the background.",
        "I'll keep an eye on that.",
        "Handling that now.",
    ],
}


class ResponseBuilder:
    """
    Centralized dialogue engine.
    
    Rules:
    - Always address the user as "Boss" (capital B) in spoken lines
    - Short confirmations may omit Boss only if under five words
    - Match F.R.I.D.A.Y. persona: crisp, loyal, high-tempo, Irish-leaning cadence
    """

    def __init__(self):
        self._last_bank = {}

    def _pick(self, bank_key: str) -> str:
        """Pick a random variant from a dialogue bank."""
        variants = BANKS.get(bank_key, [bank_key])
        return random.choice(variants)

    def acknowledge(self, is_short: bool = False) -> str:
        """Task acknowledgment — short form never uses boss."""
        if is_short:
            return self._pick("acknowledge")
        return self._pick("acknowledge_boss")

    def complete(self, is_short: bool = False) -> str:
        """Task completion — short form never uses boss."""
        if is_short:
            return self._pick("complete")
        return self._pick("complete_boss")

    def clarify(self, param_type: Optional[str] = None) -> str:
        """Clarification request — boss at end always."""
        if param_type == "song":
            return self._pick("clarify_song")
        if param_type == "app":
            return self._pick("clarify_app")
        if param_type == "contact":
            return self._pick("clarify_contact")
        if param_type == "message":
            return self._pick("clarify_message")
        return self._pick("clarify")

    def clarify_custom(self, prompt: str) -> str:
        """Custom clarification — ensures boss appears exactly once at end."""
        prompt = prompt.rstrip(",.! ")
        if prompt.lower().endswith("boss"):
            return prompt
        if "boss" in prompt.lower():
            return prompt
        return f"{prompt}, boss."

    def error(self, context: Optional[str] = None) -> str:
        """Error messages — never use boss."""
        if context == "window_context":
            return self._pick("error_window_context")
        return self._pick("error_generic")

    def thinking(self, is_short: bool = False) -> str:
        """Thinking filler — short form never uses boss."""
        if is_short:
            return self._pick("thinking")
        return self._pick("thinking_boss")

    def greeting(self, time_of_day: str = "morning") -> str:
        """Time-appropriate F.R.I.D.A.Y. greeting."""
        key = f"greeting_{time_of_day}"
        if key not in BANKS:
            from brain.friday_persona import time_of_day_label
            key = f"greeting_{time_of_day_label()}"
        if key not in BANKS:
            key = "greeting_morning"
        return self._pick(key)

    def close(self) -> str:
        """Readiness close — step 5 of speech template."""
        return random.choice([
            "What else, Boss?",
            "Standing by, Boss.",
            "Ready for the next one, Boss.",
        ])

    def intro(self) -> str:
        return self._pick("intro")

    def focus(self) -> str:
        return self._pick("focus")

    def cancel(self) -> str:
        return self._pick("cancel")

    def news_start(self) -> str:
        return self._pick("news_start")

    def background(self) -> str:
        return self._pick("background")

    def action_template(self, template: str, params: Optional[dict] = None) -> str:
        """
        Fill an action template string with parameters.
        Ensures "boss" rules are respected in the final output.
        Example: "Opening {app}." + params={"app": "Chrome"} → "Opening Chrome."
        """
        if params:
            try:
                template = template.format(**params)
            except KeyError:
                pass
        return template

    def format_with_boss_rule(self, text: str, use_boss: Optional[bool] = None) -> str:
        """
        Apply the "boss" rules to an arbitrary text string.
        If use_boss is None, infers based on text length and content.
        """
        text = text.strip()
        boss_count = text.lower().count("boss")

        if use_boss is False:
            # Strip all boss occurrences
            text = text.replace("boss, ", "").replace(", boss", "").replace("boss", "").strip()
            text = text.rstrip(", ").strip()
            return text

        should_have_boss = use_boss
        if should_have_boss is None:
            words = text.split()
            should_have_boss = len(words) >= 5 and "?" not in text

        if boss_count == 0 and should_have_boss:
            text = text.rstrip(".!?")
            text = f"{text}, boss."
        elif boss_count > 1:
            first_boss = text.lower().find("boss")
            second_boss = text.lower().find("boss", first_boss + 4)
            if second_boss != -1:
                before = text[:second_boss]
                after = text[second_boss + 4:]
                after = after.lstrip(", ")
                text = (before + after).strip()
                text = text.rstrip(", ")

        return text


# Singleton for convenience
_builder = None


def get_builder() -> ResponseBuilder:
    global _builder
    if _builder is None:
        _builder = ResponseBuilder()
    return _builder
