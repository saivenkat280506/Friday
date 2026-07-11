"""
browser_agent_process.py — Spawn and monitor the Node Puppeteer sidecar.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys

from paths import PROJECT_ROOT

logger = logging.getLogger("friday.browser_agent.process")

_process: subprocess.Popen | None = None


def _browser_agent_dir() -> str:
    return os.path.join(PROJECT_ROOT, "browser-agent")


def _node_command() -> list[str]:
    agent_dir = _browser_agent_dir()
    dist = os.path.join(agent_dir, "dist", "server.js")
    src = os.path.join(agent_dir, "src", "server.ts")
    if os.path.isfile(dist):
        return ["node", dist]
    if os.path.isfile(src):
        npx = "npx.cmd" if sys.platform == "win32" else "npx"
        return [npx, "tsx", src]
    raise FileNotFoundError(f"browser-agent entry not found under {agent_dir}")


async def start_browser_agent_process() -> bool:
    """Start the Node sidecar if not already running."""
    global _process
    from executor.browser_agent_client import is_browser_agent_available

    if await is_browser_agent_available():
        logger.info("Browser agent already running")
        return True

    if _process and _process.poll() is None:
        return True

    cmd = _node_command()
    agent_dir = _browser_agent_dir()
    env = os.environ.copy()
    env.setdefault("BROWSER_AGENT_PORT", "9477")

    try:
        _process = subprocess.Popen(
            cmd,
            cwd=agent_dir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except Exception as exc:
        logger.error("Failed to start browser agent: %s", exc)
        return False

    for _ in range(20):
        await asyncio.sleep(0.5)
        if await is_browser_agent_available():
            logger.info("Browser agent started (pid=%s)", _process.pid)
            return True
        if _process.poll() is not None:
            err = (_process.stderr.read() or b"").decode(errors="replace")
            logger.error("Browser agent exited early: %s", err[:500])
            return False

    logger.warning("Browser agent health check timed out")
    return False


async def stop_browser_agent_process() -> None:
    global _process
    if _process and _process.poll() is None:
        _process.terminate()
        try:
            _process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _process.kill()
    _process = None