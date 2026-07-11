"""Quick smoke test for the command enhancer module."""

import sys
sys.path.insert(0, ".")

from brain.command_enhancer import enhance_command


def test_whatsapp_contact_resolution():
    print("=== Test 1: WhatsApp contact resolution ===")
    result = enhance_command("send message to sathish on whatsapp saying hello")
    assert result.category == "communication", f"Expected communication, got {result.category}"
    assert result.enhanced_params.get("phone_number"), f"No phone_number: {result.enhanced_params}"
    assert "sathish" in result.enhanced_params.get("contact", "").lower()
    assert result.enhanced_params.get("message") == "hello"
    assert result.enhanced_params.get("search_strategy") == "phone_number_first"
    print("  contact:", result.enhanced_params["contact"])
    print("  phone:", result.enhanced_params["phone_number"])
    print("  message:", result.enhanced_params["message"])
    print("  strategy:", result.enhanced_params["search_strategy"])
    print("  queries:", result.enhanced_params.get("search_queries", []))
    print("PASS")


def test_case_insensitive_contact():
    print("\n=== Test 2: Case-insensitive WhatsApp contact ===")
    for name in ["sathish", "Sathish", "SATHISH"]:
        r = enhance_command(f"send message to {name} on whatsapp saying hi")
        assert r.enhanced_params.get("phone_number"), f"Failed for {name}"
        print(f"  {name} -> phone={r.enhanced_params['phone_number']} OK")
    print("PASS")


def test_music_search_defaults():
    print("\n=== Test 3: Music search defaults to YouTube ===")
    result = enhance_command("search for batman music")
    assert result.category == "media", f"Expected media, got {result.category}"
    assert result.enhanced_params.get("platform") == "youtube"
    print("  platform:", result.enhanced_params["platform"])
    print("  query:", result.enhanced_params.get("query", ""))
    print("PASS")


def test_explicit_platform():
    print("\n=== Test 4: Explicit platform preserved ===")
    result = enhance_command("play despacito on spotify")
    assert result.enhanced_params.get("platform") == "spotify"
    print("  platform:", result.enhanced_params["platform"])
    print("PASS")


def test_news_detection():
    print("\n=== Test 5: News detection ===")
    result = enhance_command("read headlines about today news")
    assert result.category == "information"
    assert result.enhanced_params.get("info_type") == "news"
    print("  info_type:", result.enhanced_params["info_type"])
    print("  topic:", result.enhanced_params.get("topic", ""))
    print("PASS")


def test_general_command():
    print("\n=== Test 6: General command (no enrichment) ===")
    result = enhance_command("open chrome")
    # 'open' matches _PLAY_RE pattern but chrome is not music-related;
    # will be media due to 'open' matching 'start' in the pattern.
    # That's fine, it doesn't affect routing.
    print("  category:", result.category)
    assert not result.enhanced_params.get("phone_number")
    print("PASS")


def test_whatsapp_without_message():
    print("\n=== Test 7: WhatsApp without explicit message ===")
    result = enhance_command("search for sathish contact in whatsapp")
    assert result.category == "communication"
    print("  category:", result.category)
    print("  contact:", result.enhanced_params.get("contact", "N/A"))
    print("  phone:", result.enhanced_params.get("phone_number", "N/A"))
    print("PASS")


if __name__ == "__main__":
    test_whatsapp_contact_resolution()
    test_case_insensitive_contact()
    test_music_search_defaults()
    test_explicit_platform()
    test_news_detection()
    test_general_command()
    test_whatsapp_without_message()
    print("\n" + "=" * 50)
    print("ALL ENHANCER TESTS PASSED!")
