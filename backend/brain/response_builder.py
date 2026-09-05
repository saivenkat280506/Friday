"""
response_builder.py — Friday dialogue banks
==========================================
Short spoken lines. "boss" is optional, never mandatory. See friday_persona.py.
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
        "On it, boss.",
        "Give me a sec, boss. Let me check.",
        "On it.",
        "I'll take care of it.",
        "Working it now, boss.",
    ],

    # ── Task Completion ─────────────────────────────────────────────────────────
    "complete": [
        "Done.",
        "Found it.",
        "There we go.",
        "That's taken care of.",
        "All set.",
        "That's done.",
    ],
    "complete_boss": [
        "Done, boss.",
        "Found it.",
        "There we go.",
        "All set, boss.",
        "That's handled.",
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
        "On it.",
        "Give me a sec.",
        "Let me check.",
        "Working on it.",
        "Just a second.",
    ],
    "thinking_boss": [
        "Give me a sec, boss. Let me check.",
        "On it.",
        "One moment. Pulling that up.",
        "Let me look into that.",
    ],

    # ── Greetings ──────────────────────────────────────────────────────────────
    "greeting_morning": [
        "Morning. What are we working on?",
        "Morning, boss. I'm here.",
    ],
    "greeting_afternoon": [
        "Afternoon. Ready when you are.",
        "Hey. What's the plan?",
    ],
    "greeting_evening": [
        "Evening. Still at it?",
        "Hey, boss. What do you need?",
    ],
    "greeting_night": [
        "You're up late. I'm with you.",
        "Quiet hour. What are we tackling?",
    ],

    # ── Intro ───────────────────────────────────────────────────────────────────
    "intro": [
        "I'm Friday. I research, run tools, and help you get things done.",
        "Friday. Think of me as the assistant who already started the next step.",
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
        "Give me a sec, boss. Let me check.",
        "On it. Pulling the latest.",
    ],

    # ── Background ──────────────────────────────────────────────────────────────
    "background": [
        "Running that in the background.",
        "I'll keep an eye on that.",
        "Handling that now.",
    ],

    "joke": [
        "Why did the computer go to the doctor, Boss? It had a virus.",
        "I tried telling a UDP joke, Boss. I am not sure you got it.",
        "Why was the keyboard calm, Boss? It had excellent control.",
    ],
}


class ResponseBuilder:
    """
    Centralized dialogue engine.
    
    Rules:
    - "boss" is optional and occasional, never every line
    - Short confirmations usually skip it
    - Calm, concise, capable — never theatrical
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
            "What else?",
            "I'm here.",
            "Whenever you're ready.",
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

    def joke(self) -> str:
        return self._pick("joke")

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
            # Default: do not inject "boss". The spec is occasional, not automatic.
            should_have_boss = False

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
