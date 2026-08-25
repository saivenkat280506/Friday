"""Browser automation integration test (sidecar + Python client)."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from executor.browser_agent import _route_recipe, run_browser_agent, run_browser_recipe
from executor.browser_agent_client import get_browser_client, is_browser_agent_available


def test_route_parsing() -> list[str]:
    errors: list[str] = []
    cases = [
        ("Research news about space exploration", "researchNews", "space exploration"),
        ("Play Blinding Lights on Spotify", "playSpotify", "Blinding Lights"),
        ("Set Spotify volume to 35%", "setSpotifyVolume", None),
    ]
    for task, expected_recipe, expected_param in cases:
        routed = _route_recipe(task)
        if not routed:
            errors.append(f"route failed for: {task}")
            continue
        recipe, params = routed
        if recipe != expected_recipe:
            errors.append(f"route {task!r} -> {recipe}, expected {expected_recipe}")
        if expected_param and expected_param.lower() not in json.dumps(params).lower():
            errors.append(f"route params for {task!r}: {params}")
    return errors


async def main() -> int:
    import os

    os.environ["FRIDAY_BROWSER_TEST"] = "1"
    errors = test_route_parsing()

    if not await is_browser_agent_available():
        errors.append("browser-agent sidecar not reachable on :9477")
        print(json.dumps({"ok": False, "errors": errors}, indent=2))
        return 1

    client = get_browser_client()
    health = await client.health()
    print("health:", json.dumps(health))

    await client.start_session("headless")

    obs = await client.observe()
    if not obs.get("success") or not obs.get("observation", {}).get("screenshot_path"):
        errors.append(f"observe failed or missing screenshot: {obs.get('message')}")

    action = await client.action(
        {"action": "SEARCH", "query": "FRIDAY AI assistant", "engine": "google"},
        mode="headless",
    )
    if not action.get("success"):
        errors.append(f"SEARCH failed: {action.get('message')}")

    shot = await client.screenshot()
    if not shot.get("screenshot_base64"):
        errors.append("screenshot endpoint missing base64")

    recipe = await client.recipe(
        "researchNews",
        {"topic": "electric vehicles"},
        task="research news EV",
        mode="headless",
    )
    if not recipe.get("success"):
        errors.append(f"researchNews failed: {recipe.get('message')}")
    headlines = (recipe.get("observation") or {}).get("extracted_data", {}).get("headlines", [])
    if len(headlines) < 2:
        errors.append(f"researchNews too few headlines: {headlines}")

    ok, msg = await run_browser_recipe(
        "googleSearch",
        {"query": "Puppeteer automation"},
        task="google search test",
        mode="headless",
    )
    if not ok:
        errors.append(f"googleSearch recipe: {msg}")

    ok2, msg2 = await run_browser_agent(
        "Research news about space exploration",
        mode="headless",
        max_steps=4,
    )
    if not ok2:
        errors.append(f"agent loop: {msg2}")
    elif "explorati" in msg2.lower() and "exploration" not in msg2.lower():
        errors.append(f"topic truncation bug: {msg2}")

    spotify = await client.recipe(
        "playSpotify",
        {"song": "Blinding Lights The Weeknd"},
        task="spotify test",
        mode="headless",
    )
    if not spotify.get("success"):
        errors.append(f"playSpotify failed: {spotify.get('message')}")

    await client.stop_session()

    result = {
        "ok": not errors,
        "errors": errors,
        "samples": {
            "observe_url": (obs.get("observation") or {}).get("current_url"),
            "search_url": (action.get("observation") or {}).get("current_url"),
            "headlines": headlines[:3],
            "spotify_msg": spotify.get("message", "")[:120],
            "agent_msg": msg2[:200],
        },
    }
    print(json.dumps(result, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))