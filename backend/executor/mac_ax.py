"""
mac_ax.py — Phase 4 macOS Accessibility Driver
===============================================

Real macOS UI control using native Accessibility API & System Events.
Controls UI elements by semantic role/label rather than just blind coordinates.

Doc §12 & §13 Phase 4:
  - macOS Accessibility driver
  - Click named buttons, menu items, tabs
  - Read active UI elements (buttons, inputs, static texts)
  - Set text field values

Usage:
    from executor.mac_ax import click_button, click_menu_item, get_ui_elements, set_text_field

    success, msg = click_button("Save")
    success, msg = click_menu_item("File", "Save")
    elements = get_ui_elements()
"""

from __future__ import annotations

import logging
import platform
import subprocess
import json
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("friday.executor.mac_ax")

IS_MAC = platform.system().lower() == "darwin"


@dataclass
class UIElement:
    role: str
    title: str
    description: str
    enabled: bool = True
    focused: bool = False


def _run_applescript(script: str, timeout: float = 3.0) -> tuple[bool, str]:
    """Execute AppleScript safely with timeout."""
    if not IS_MAC:
        return False, "Accessibility driver is only supported on macOS."
    try:
        res = subprocess.check_output(
            ["osascript", "-e", script],
            timeout=timeout,
            stderr=subprocess.PIPE,
            text=True,
        ).strip()
        return True, res
    except subprocess.TimeoutExpired:
        return False, "AppleScript timed out"
    except subprocess.CalledProcessError as exc:
        err = exc.stderr.strip() if exc.stderr else str(exc)
        logger.debug("[MacAX] script error: %s", err)
        return False, err
    except Exception as exc:
        return False, str(exc)


def click_button(label: str, app_name: str | None = None) -> tuple[bool, str]:
    """
    Click a button by its text label in the frontmost app or named app.
    """
    escaped_label = label.replace('"', '\\"')
    if app_name:
        escaped_app = app_name.replace('"', '\\"')
        target = f'process "{escaped_app}"'
    else:
        target = 'first application process whose frontmost is true'

    script = f'''
    tell application "System Events"
        tell {target}
            set targetWindow to front window
            click (first button of targetWindow whose name is "{escaped_label}" or description is "{escaped_label}")
        end tell
    end tell
    '''
    success, out = _run_applescript(script)
    if success:
        return True, f"Clicked '{label}' button."
    return False, f"Could not find or click button '{label}': {out}"


def click_menu_item(menu_name: str, item_name: str, app_name: str | None = None) -> tuple[bool, str]:
    """
    Click a menu bar item (e.g. File > New Window).
    """
    escaped_menu = menu_name.replace('"', '\\"')
    escaped_item = item_name.replace('"', '\\"')
    if app_name:
        escaped_app = app_name.replace('"', '\\"')
        target = f'process "{escaped_app}"'
    else:
        target = 'first application process whose frontmost is true'

    script = f'''
    tell application "System Events"
        tell {target}
            click menu item "{escaped_item}" of menu "{escaped_menu}" of menu bar 1
        end tell
    end tell
    '''
    success, out = _run_applescript(script)
    if success:
        return True, f"Clicked '{menu_name} > {item_name}'."
    return False, f"Could not click menu item '{menu_name} > {item_name}': {out}"


def set_text_field(value: str, field_index: int = 1, app_name: str | None = None) -> tuple[bool, str]:
    """
    Set value of text field in front window.
    """
    escaped_val = value.replace('"', '\\"')
    if app_name:
        escaped_app = app_name.replace('"', '\\"')
        target = f'process "{escaped_app}"'
    else:
        target = 'first application process whose frontmost is true'

    script = f'''
    tell application "System Events"
        tell {target}
            set targetWindow to front window
            set value of text field {field_index} of targetWindow to "{escaped_val}"
        end tell
    end tell
    '''
    success, out = _run_applescript(script)
    if success:
        return True, f"Set text field {field_index} to '{value}'."
    return False, f"Could not set text field: {out}"


def get_ui_elements(app_name: str | None = None, max_items: int = 20) -> list[UIElement]:
    """
    Inspect front window and return list of interactive UI elements.
    """
    if app_name:
        escaped_app = app_name.replace('"', '\\"')
        target = f'process "{escaped_app}"'
    else:
        target = 'first application process whose frontmost is true'

    script = f'''
    tell application "System Events"
        tell {target}
            if exists (front window) then
                tell front window
                    set btnNames to name of every button
                    set txtFields to name of every text field
                    return btnNames
                end tell
            end if
        end tell
    end tell
    '''
    success, out = _run_applescript(script)
    elements: list[UIElement] = []
    if success and out:
        for item in out.split(","):
            name = item.strip()
            if name and name != "missing value":
                elements.append(UIElement(role="button", title=name, description=name))
    return elements[:max_items]
