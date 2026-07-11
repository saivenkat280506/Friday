"""
service_config.py — Toggle background services at boot time.

Each service can be disabled with an environment variable:

    FRIDAY_SERVICE_WATCHDOG=0
    FRIDAY_SERVICE_AGENT_LOOP=0
    FRIDAY_SERVICE_VISION_LOOP=0
    FRIDAY_SERVICE_PROCESS_MONITOR=0
    FRIDAY_SERVICE_BACKGROUND_MONITOR=0
    FRIDAY_SERVICE_VOICE_LOOP=0
    FRIDAY_SERVICE_TTS_WARMUP=0
    FRIDAY_SERVICE_BROWSER_AGENT=0

Unset variables default to enabled. Vision loop defaults to disabled. Values ``0``, ``false``, ``no``, and
``off`` disable a service; ``1``, ``true``, ``yes``, and ``on`` enable it.

Future UI settings can feed the same flags via ``from_settings()``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_enabled(name: str, *, default: bool = True) -> bool:
    """Return whether a service is enabled from ``FRIDAY_SERVICE_<NAME>``."""
    raw = os.getenv(f"FRIDAY_SERVICE_{name.upper()}", "").strip().lower()
    if not raw:
        return default
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    return default


@dataclass(frozen=True)
class ServiceConfig:
    """Per-service enable flags evaluated once at application startup."""

    watchdog: bool = True
    agent_loop: bool = True
    vision_loop: bool = False
    process_monitor: bool = True
    background_monitor: bool = True
    voice_loop: bool = True
    tts_warmup: bool = True
    browser_agent: bool = True

    def is_enabled(self, service_name: str) -> bool:
        """Return whether ``service_name`` (registry key) should start."""
        return getattr(self, service_name, True)

    @classmethod
    def from_env(cls) -> ServiceConfig:
        """Build config from ``FRIDAY_SERVICE_*`` environment variables."""
        return cls(
            watchdog=_env_enabled("watchdog"),
            agent_loop=_env_enabled("agent_loop"),
            vision_loop=_env_enabled("vision_loop", default=False),
            process_monitor=_env_enabled("process_monitor"),
            background_monitor=_env_enabled("background_monitor"),
            voice_loop=_env_enabled("voice_loop"),
            tts_warmup=_env_enabled("tts_warmup"),
            browser_agent=_env_enabled("browser_agent"),
        )

    @classmethod
    def from_settings(cls, settings: dict) -> ServiceConfig:
        """
        Build config from a settings dict (e.g. ``brain.settings``).

        Expects optional ``services`` key::

            { "services": { "watchdog": false, "voice_loop": true } }
        """
        overrides = settings.get("services") or {}
        base = cls.from_env()
        if not overrides:
            return base
        return cls(
            watchdog=overrides.get("watchdog", base.watchdog),
            agent_loop=overrides.get("agent_loop", base.agent_loop),
            vision_loop=overrides.get("vision_loop", base.vision_loop),
            process_monitor=overrides.get("process_monitor", base.process_monitor),
            background_monitor=overrides.get("background_monitor", base.background_monitor),
            voice_loop=overrides.get("voice_loop", base.voice_loop),
            tts_warmup=overrides.get("tts_warmup", base.tts_warmup),
            browser_agent=overrides.get("browser_agent", base.browser_agent),
        )