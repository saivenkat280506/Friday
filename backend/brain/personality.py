"""
personality.py — Friday Persona Module
======================================
Provides precise, confident, and respectful responses in the style of F.R.I.D.A.Y.
"""

import random
from datetime import datetime

RESPONSE_MAP = {
    "chat": {
        "start": ["{response}"],
        "success": [""],
        "fail": ["I'm sorry, I couldn't respond to that."]
    },
    "open_app": {
        "start": ["Opening {app}, Boss.", "Will do, Boss.", "As you wish."],
        "success": ["Done, Boss.", "Ready, Boss.", "Check."],
        "fail": ["I couldn't open {app}, Boss.", "Unable to locate {app}, Boss."]
    },
    "send_whatsapp": {
        "start": ["Opening WhatsApp and loading the chat, Boss."],
        "success": ["WhatsApp message sent, Boss."],
        "fail": ["Could not open WhatsApp contact, Boss."]
    },
    "send_whatsapp_all": {
        "start": ["Sending to saved contacts only, Boss."],
        "success": ["Sent to the saved contacts, Boss."],
        "fail": ["I could not finish sending to every saved contact, Boss."]
    },
    "remember": {
        "start": ["Saving that, Boss."],
        "success": ["Noted, Boss."],
        "fail": ["I could not save that, Boss."]
    },
    "recall": {
        "start": ["Checking memory, Boss."],
        "success": ["Here it is, Boss."],
        "fail": ["I do not have that saved, Boss."]
    },
    "confirm_whatsapp_send": {
        "start": ["Sending now, Boss."],
        "success": ["Message sent, Boss.", "Delivered, Boss.", "Transmission complete, Boss."],
        "fail": ["Could not send the message, Boss.", "Send failed, Boss."]
    },
    "cancel_whatsapp_send": {
        "start": ["Cancelling the draft, Boss."],
        "success": ["Draft cancelled — nothing was sent, Boss.", "Understood. Message not sent, Boss."],
        "fail": ["Could not clear the draft, Boss."]
    },
    "play_local_music": {
        "start": ["Starting garage music, Boss.", "Playing your local track, Boss.", "Right away, Boss."],
        "success": ["Garage music is playing, Boss.", "Now playing, Boss.", "Audio stream established, Boss."],
        "fail": ["Couldn't find the local track, Boss.", "Playback failed, Boss."],
    },
    "daddys_home": {
        "start": ["Welcome home, Boss."],
        "success": ["Welcome home, Boss."],
        "fail": ["Welcome home, Boss. Local audio failed, but I am still online."],
    },
    "play_youtube_music": {
        "start": ["Playing {song} on YouTube Music, Boss.", "Queueing {song} on YouTube Music, Boss.", "Starting playback, Boss."],
        "success": ["Now playing on YouTube Music, Boss.", "All set, Boss.", "Playing now, Boss."],
        "fail": ["Could not find {song}, Boss.", "Playback failed, Boss."],
        "again": ["Playing it again, Boss.", "Replaying {song}, Boss.", "One more time, Boss."]
    },
    "play_youtube_search": {
        "start": ["Searching YouTube for {song}, Boss.", "Looking up {song} on YouTube, Boss."],
        "success": ["YouTube search opened, Boss.", "Ready to play, Boss."],
        "fail": ["Couldn't open YouTube, Boss.", "Search failed, Boss."],
    },
    "play_spotify": {
        "start": ["Searching Spotify for {song}, Boss.", "Looking up {song} on Spotify, Boss."],
        "success": ["Spotify search opened, Boss.", "Ready to play, Boss."],
        "fail": ["Couldn't open Spotify, Boss.", "Search failed, Boss."],
    },
    "music_control": {
        "start": ["Adjusting music, Boss."],
        "success": ["Done, Boss."],
        "fail": ["Music control failed, Boss."],
    },
    "volume_control": {
        "start": ["Adjusting volume, Boss."],
        "success": ["Volume updated, Boss."],
        "fail": ["Couldn't adjust volume, Boss."],
    },
    "search_browser": {
        "start": ["Looking that up, Boss.", "Searching, Boss.", "On it."],
        "success": ["Query complete, Boss.", "Found it, Boss.", "Results are ready, Boss."],
        "fail": ["Nothing useful, Boss.", "I couldn't find that, Boss."]
    },
    "general": {
        "start": ["On it, Boss.", "Working on it, Boss.", "As you wish."],
        "success": ["Done, Boss.", "All set, Boss.", "Check."],
        "fail": ["I couldn't complete that, Boss.", "That didn't take, Boss."]
    },
    "news": {
        "start": ["Checking the latest headlines, Boss.", "Pulling the news feed, Boss.", "Scanning current events."],
        "success": ["Here's what's happening right now, Boss.", "Latest briefing ready, Boss."],
        "fail": ["Couldn't retrieve headlines at this moment, Boss."]
    },
    "joke": {
        "start": ["One moment, Boss.", "Let me think of something appropriate."],
        "success": [""],
        "fail": ["My humor subroutines appear to be offline, Boss."]
    },
    "intro": {
        "start": ["Of course, Boss."],
        "success": [""],
        "fail": ["I seem to be having trouble with self-reflection, Boss."]
    },
    "focus_window": {
        "start": ["Returning to the interface, Boss."],
        "success": ["Back in focus, Boss."],
        "fail": ["Couldn't restore focus, Boss."]
    },
    "background": {
        "start": [
            "I’ll handle that, Boss.",
            "Running in background, Boss.",
            "Processing that in the background, Boss.",
            "I'll keep an eye on that, Boss."
        ]
    },
    "cancel": {
        "success": [
            "Stopped, Boss.",
            "Cancelled, Boss.",
            "Task terminated, Boss."
        ],
        "fail": [
            "Nothing running to stop, Boss.",
            "No active tasks to cancel, Boss."
        ]
    }
}

def _get_template(intent: str, phase: str) -> str:
    """Retrieves a random template for a given intent and phase."""
    category = RESPONSE_MAP.get(intent, RESPONSE_MAP["general"])
    return random.choice(category.get(phase, RESPONSE_MAP["general"][phase]))

def respond_start(intent: str, params: dict = None) -> str:
    """Immediate feedback before action starts."""
    template = _get_template(intent, "start")
    params = params or {}
    try:
        return template.format(**params)
    except KeyError:
        return template

def respond_success(intent: str, params: dict = None) -> str:
    """Confirmation after successful action."""
    # Check for "again" context in params
    if params and params.get("is_again"):
        template = random.choice(RESPONSE_MAP.get(intent, RESPONSE_MAP["general"]).get("again", ["Done, Boss."]))
        try:
            return template.format(**params)
        except KeyError:
            return template
    
    template = _get_template(intent, "success")
    params = params or {}
    try:
        return template.format(**params)
    except KeyError:
        return template

def respond_fail(intent: str, params: dict = None) -> str:
    """Respectful failure notification."""
    return "I couldn’t complete that, Boss."

def respond_background(intent: str = None, params: dict = None) -> str:
    """Notification for non-blocking background tasks."""
    return random.choice(RESPONSE_MAP["background"]["start"])

def respond_cancel(success: bool = True) -> str:
    """Response for task cancellation."""
    if success:
        return random.choice(RESPONSE_MAP["cancel"]["success"])
    else:
        return random.choice(RESPONSE_MAP["cancel"]["fail"])

def respond_processing() -> str:
    """Immediate response when processing takes time (LLM route)."""
    return random.choice([
        "On it.",
        "Give me a sec, boss. Let me check.",
        "Working on it.",
        "Just a second.",
    ])


def _daypart(now: datetime | None = None) -> str:
    """early_morning | morning | noon | afternoon | evening | night | late_night"""
    hour = (now or datetime.now()).hour
    if 5 <= hour < 9:
        return "early_morning"
    if 9 <= hour < 12:
        return "morning"
    if 12 <= hour < 14:
        return "noon"
    if 14 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 21:
        return "evening"
    if 21 <= hour or hour < 2:
        return "night"
    return "late_night"


def _clock_label(now: datetime | None = None) -> str:
    """Spoken-friendly clock, e.g. '11:42 PM'."""
    now = now or datetime.now()
    hour12 = now.hour % 12 or 12
    meridiem = "AM" if now.hour < 12 else "PM"
    return f"{hour12}:{now.minute:02d} {meridiem}"


def launch_greeting_line(now: datetime | None = None) -> str:
    """
    First line FRIDAY speaks when the desktop app comes online.
    Canonical lines plus close variants, keyed to the local hour.
    """
    now = now or datetime.now()
    part = _daypart(now)
    clock = _clock_label(now)
    weekday = now.strftime("%A")
    weekend = weekday in ("Saturday", "Sunday")

    if part == "early_morning":
        lines = [
            "You're in early. What are we working on?",
            "Morning, boss. Quiet hour. I'm here.",
            "Early start. Ready when you are.",
        ]
        if weekday == "Monday":
            lines.append("Monday morning. Let's make it a clean one.")
        if weekend:
            lines.append("Weekend morning. You're up early.")
        return random.choice(lines)

    if part == "morning":
        lines = [
            "Morning. What are we working on?",
            "Morning, boss. I'm here.",
            "Hey. What's the plan?",
        ]
        if weekend:
            lines.append("Weekend already. What do you need?")
        return random.choice(lines)

    if part == "noon":
        return random.choice([
            "Midday. What do you need?",
            f"It's {clock}. Still going?",
            "Noon already. I'm here.",
        ])

    if part == "afternoon":
        return random.choice([
            "Afternoon. Ready when you are.",
            "Hey. What's the plan?",
            "I'm here, boss. What do you need?",
        ])

    if part == "evening":
        lines = [
            "Evening. Still at it?",
            f"It's {clock}. What are we working on?",
            "Hey, boss. What's next?",
        ]
        if weekday == "Friday":
            lines.append("Friday evening. Still going, or wrapping up?")
        return random.choice(lines)

    # night / late night
    lines = [
        f"It's {clock}. Working on a project?",
        f"It's {clock}. I'm with you.",
        f"Late hour. What do you need?",
    ]
    if part == "late_night":
        lines.append(f"It's {clock}. You might want to save and stop soon. I'm here if not.")
    return random.choice(lines)


def welcome_home_line() -> str:
    """
    Classic F.R.I.D.A.Y. lines over garage music (time-aware).
    Used for "Wake up. Daddy's home." and default garage playback.
    """
    part = _daypart()
    if part in ("early_morning", "morning"):
        return random.choice([
            "Welcome home, Boss. Let me check your schedule for today. You have no pending tasks to do, Boss. Have a nice day, Boss.",
            "Welcome home, Boss. I've reviewed your itinerary. No pending tasks. Have a nice day, Boss.",
            "Good morning, Boss. Welcome home. Your calendar is clear — no pending tasks. Have a nice day, Boss.",
        ])
    if part == "noon":
        return random.choice([
            "Welcome home, Boss. It's noon, Boss. You have to take rest from your garage so you could recover from your soreness.",
            "Welcome home, Boss. Midday already. I recommend rest after the garage so you can recover from the soreness.",
            "It's noon, Boss. Welcome home. Rest from the garage, Boss — recover from your soreness.",
        ])
    if part == "afternoon":
        return random.choice([
            "Welcome home, Boss. Afternoon already. You may want rest after the garage so you recover from the soreness.",
            "Welcome home, Boss. Systems nominal. A short rest would be wise after the garage, Boss.",
        ])
    if part == "evening":
        return random.choice([
            "Welcome home, Boss. Working late, are we? Are we on a project?",
            "Welcome home, Boss. Evening already. Shall I assume we are on a project?",
        ])
    # night / late
    return random.choice([
        "Welcome home, Boss. Working late, Boss. Are we on a project?",
        "Welcome home, Boss. Burning the midnight oil. Are we on a project?",
        "Working late, Boss. Welcome home. Are we on a project tonight?",
    ])


def garage_music_line() -> str:
    """Short confirmation when the user asked to play music — not a welcome-home briefing."""
    return random.choice([
        "Playing your music, Boss.",
        "Garage track is on, Boss.",
        "Music coming up, Boss.",
    ])


if __name__ == "__main__":
    # Tests
    print(f"Open App Start: {respond_start('open_app', {'app': 'Chrome'})}")
    print(f"Msg Success: {respond_success('send_whatsapp')}")
    print(f"Fail: {respond_fail('any')}")
    print(f"BG Response: {respond_background()}")
    print(f"Launch: {launch_greeting_line()}")

