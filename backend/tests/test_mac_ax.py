"""
test_mac_ax.py — Phase 4 macOS Accessibility Driver tests

Run: python backend/tests/test_mac_ax.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from executor.mac_ax import (
    UIElement,
    click_button,
    click_menu_item,
    set_text_field,
    get_ui_elements,
)


class TestMacAccessibility(unittest.TestCase):
    @patch("executor.mac_ax._run_applescript", return_value=(True, ""))
    def test_click_button_success(self, mock_script):
        success, msg = click_button("Save")
        self.assertTrue(success)
        self.assertIn("Save", msg)

    @patch("executor.mac_ax._run_applescript", return_value=(False, "Button not found"))
    def test_click_button_failure(self, mock_script):
        success, msg = click_button("Nonexistent")
        self.assertFalse(success)
        self.assertIn("Could not find", msg)

    @patch("executor.mac_ax._run_applescript", return_value=(True, ""))
    def test_click_menu_item_success(self, mock_script):
        success, msg = click_menu_item("File", "Save")
        self.assertTrue(success)
        self.assertIn("File > Save", msg)

    @patch("executor.mac_ax._run_applescript", return_value=(True, ""))
    def test_set_text_field_success(self, mock_script):
        success, msg = set_text_field("Hello World", 1)
        self.assertTrue(success)
        self.assertIn("Hello World", msg)

    @patch("executor.mac_ax._run_applescript", return_value=(True, "OK, Cancel, Apply"))
    def test_get_ui_elements(self, mock_script):
        elements = get_ui_elements()
        self.assertEqual(len(elements), 3)
        self.assertEqual(elements[0].title, "OK")
        self.assertEqual(elements[1].title, "Cancel")
        self.assertEqual(elements[2].title, "Apply")


if __name__ == "__main__":
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if result.wasSuccessful():
        print("\n[PASS] Phase 4 mac_ax tests passed")
    else:
        print("\n[FAIL] mac_ax tests failed")
    sys.exit(0 if result.wasSuccessful() else 1)
