"""
volume_control.py — macOS system volume (0–100)
==============================================
Uses AppleScript output volume. Also manages temporary levels during
garage-music sessions (play 50% / speak 25% / listen 20%).
"""

from executor.sys_platform import (
    get_output_volume,
    is_output_muted as _is_output_muted,
    set_output_muted,
    set_output_volume,
)

GARAGE_PLAY_VOLUME = 50
GARAGE_SPEAK_VOLUME = 25
GARAGE_LISTEN_VOLUME = 20

_garage_session = False
_volume_before_garage: int | None = None


def get_volume() -> int:
    return get_output_volume()


def is_muted() -> bool:
    return _is_output_muted()


def set_volume(level: int) -> tuple[bool, str]:
    level = max(0, min(100, int(level)))
    set_output_muted(False)
    set_output_volume(level)
    return True, f"Volume set to {level}, Boss."


def begin_garage_volume_session(play_level: int = GARAGE_PLAY_VOLUME) -> int:
    global _garage_session, _volume_before_garage
    try:
        if not _garage_session:
            _volume_before_garage = get_volume()
            _garage_session = True
        set_volume(play_level)
        print(f"[Volume] Garage session ON — system volume {play_level}% (was {_volume_before_garage})")
        return play_level
    except Exception as exc:
        print(f"[Volume] begin_garage_volume_session failed: {exc}")
        return play_level


def set_system_volume(level: int) -> None:
    try:
        set_volume(int(level))
        print(f"[Volume] System master -> {int(level)}%")
    except Exception as exc:
        print(f"[Volume] set failed: {exc}")


# Kept so existing callers do not break.
set_windows_volume_safe = set_system_volume


def garage_volume_for_play() -> None:
    if _garage_session:
        set_system_volume(GARAGE_PLAY_VOLUME)


def garage_volume_for_speech() -> None:
    set_system_volume(GARAGE_SPEAK_VOLUME)


def garage_volume_for_listen() -> None:
    if _garage_session:
        set_system_volume(GARAGE_LISTEN_VOLUME)


def end_garage_volume_session(restore: bool = True) -> None:
    global _garage_session, _volume_before_garage
    if not _garage_session:
        return
    prev = _volume_before_garage
    _garage_session = False
    _volume_before_garage = None
    if restore and prev is not None:
        set_system_volume(prev)
        print(f"[Volume] Garage session OFF — restored {prev}%")


def is_garage_volume_session() -> bool:
    return _garage_session


def adjust_volume(delta: int) -> tuple[bool, str]:
    current = get_volume()
    new_level = max(0, min(100, current + int(delta)))
    set_output_muted(False)
    set_output_volume(new_level)
    direction = "increased" if delta > 0 else "decreased"
    return True, f"Volume {direction} to {new_level}, Boss."


def mute_volume() -> tuple[bool, str]:
    set_output_muted(True)
    return True, "Volume muted, Boss."


def unmute_volume() -> tuple[bool, str]:
    set_output_muted(False)
    level = get_volume()
    return True, f"Volume unmuted at {level}, Boss."


def volume_control(params: dict) -> tuple[bool, str]:
    """
    Unified volume handler.
    Params:
      action: get | set | up | down | mute | unmute
      level:  target 0–100 (for set)
      amount: step size (for up/down, default 10)
    """
    action = (params.get("action") or "").lower()

    try:
        if action == "get":
            muted = is_muted()
            level = get_volume()
            if muted:
                return True, f"Volume is muted. Level before mute was {level}, Boss."
            return True, f"Current volume is {level}, Boss."

        if action == "set":
            level = params.get("level")
            if level is None:
                return False, "Please specify a volume level between 0 and 100, Boss."
            return set_volume(int(level))

        if action == "up":
            amount = int(params.get("amount", 10))
            return adjust_volume(abs(amount))

        if action == "down":
            amount = int(params.get("amount", 10))
            return adjust_volume(-abs(amount))

        if action == "mute":
            return mute_volume()

        if action == "unmute":
            return unmute_volume()

        return False, "Unknown volume action, Boss."
    except Exception as exc:
        return False, f"Volume control failed: {exc}"
