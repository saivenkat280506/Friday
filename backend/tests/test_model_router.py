"""
test_model_router.py — Phase 6 Model Router tests

Run: python backend/tests/test_model_router.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from brain.model_router import resolve_llm_model, FAST_MODEL, HEAVY_MODEL
from brain.state import IntentCategory


class TestModelRouter(unittest.TestCase):
    def test_planning_always_heavy(self):
        model = resolve_llm_model(IntentCategory.CHAT, for_plan=True)
        self.assertEqual(model, HEAVY_MODEL)

    def test_classify_always_fast(self):
        model = resolve_llm_model(IntentCategory.CHAT, for_classify=True)
        self.assertEqual(model, FAST_MODEL)

    def test_heavy_intents(self):
        for intent in [
            IntentCategory.NEWS,
            IntentCategory.SEARCH_WEB,
            IntentCategory.SUMMARISE,
            IntentCategory.EXPLAIN,
            IntentCategory.CODE_HELP,
        ]:
            model = resolve_llm_model(intent)
            self.assertEqual(model, HEAVY_MODEL, f"Intent {intent} should route to heavy model")

    def test_fast_intents(self):
        for intent in [
            IntentCategory.MOUSE_CLICK,
            IntentCategory.KEYBOARD_TYPE,
            IntentCategory.OPEN_APP,
            IntentCategory.VOLUME_SET,
            IntentCategory.TIME_DATE,
        ]:
            model = resolve_llm_model(intent)
            self.assertEqual(model, FAST_MODEL, f"Intent {intent} should route to fast model")

    def test_short_chat_is_fast(self):
        model = resolve_llm_model(IntentCategory.CHAT, cleaned_input="how are you today")
        self.assertEqual(model, FAST_MODEL)

    def test_long_chat_is_heavy(self):
        long_text = "can you please explain the detailed history and architecture of the Apollo guidance computer and how memory was woven by hand by core rope engineers in the 1960s"
        model = resolve_llm_model(IntentCategory.CHAT, cleaned_input=long_text)
        self.assertEqual(model, HEAVY_MODEL)


if __name__ == "__main__":
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if result.wasSuccessful():
        print("\n[PASS] Phase 6 model router — all tests passed")
    else:
        print("\n[FAIL] Phase 6 tests failed")
    sys.exit(0 if result.wasSuccessful() else 1)
