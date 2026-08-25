from brain.router import IntentRouter
from brain.state import IntentCategory
from executor.music_player import parse_music_command


def test_headlines_use_the_news_intent_with_a_real_topic():
    result = IntentRouter().classify_rules("read headlines")

    assert result.intent is IntentCategory.NEWS
    assert result.params == {"query": "latest news"}


def test_joke_request_stays_in_conversation():
    result = IntentRouter().classify_rules("tell me a joke")

    assert result.intent is IntentCategory.CHAT


def test_explain_routes_factual_questions():
    result = IntentRouter().classify_rules("what is cloud computing")

    assert result.intent is IntentCategory.EXPLAIN


def test_stt_joke_mishear_normalizes_to_joke_request():
    from brain.friday_graph import _normalize_stt_phrasing
    from brain.friday_persona import is_joke_request

    assert _normalize_stt_phrasing("i'm your joke") == "tell me a joke"
    assert is_joke_request(_normalize_stt_phrasing("i'm your joke"))


def test_new_project_routes_to_vscode_fresh_workspace():
    result = IntentRouter().classify_rules("I want to work on a new project")

    assert result.intent.value == "open_app"
    assert result.params.get("fresh_workspace") is True
    assert result.params.get("project_name") == ""


def test_new_project_named_workspace():
    result = IntentRouter().classify_rules("start a new project called cloud-api")

    assert result.intent.value == "open_app"
    assert result.params.get("fresh_workspace") is True
    assert result.params.get("project_name") == "cloud-api"


def test_fresh_project_dir_is_created():
    from executor.open_app import _fresh_project_dir

    folder = _fresh_project_dir("test-friday-workspace")
    assert folder.is_dir()
    assert (folder / "README.md").is_file()


def test_factual_prompt_omits_active_window_context():
    from brain.friday_persona import build_chat_system_prompt

    prompt = build_chat_system_prompt(
        active_window="Visual Studio Code - project.py",
        user_input="what is cloud computing",
        intent="explain",
    )

    assert "Active window: Visual Studio Code" not in prompt
    assert "FACTUAL Q&A MODE" in prompt
    assert "Do NOT say" in prompt and "give me a sec" in prompt


def test_introduce_yourself_stays_in_conversation():
    for phrase in (
        "introduce yourself",
        "who are you",
        "tell me about yourself",
    ):
        result = IntentRouter().classify_rules(phrase)
        assert result.intent is IntentCategory.CHAT, phrase


def test_local_music_never_becomes_a_spotify_search():
    assert parse_music_command("play music from local") == {
        "song": "",
        "platform": "local",
    }


def test_bare_play_music_defaults_to_local_library():
    assert parse_music_command("play music") == {
        "song": "",
        "platform": "local",
    }
    assert parse_music_command("play some music") == {
        "song": "",
        "platform": "local",
    }


def test_garage_music_uses_local_library():
    assert parse_music_command("play garage music") == {
        "song": "",
        "platform": "local",
    }


def test_hybrid_model_router():
    from brain.model_router import FAST_MODEL, HEAVY_MODEL, resolve_llm_model
    from brain.state import IntentCategory

    assert resolve_llm_model(IntentCategory.TIME_DATE, {}) == FAST_MODEL
    assert resolve_llm_model(IntentCategory.NEWS, {}) == HEAVY_MODEL
    assert resolve_llm_model(IntentCategory.SEARCH_WEB, {"query": "news"}) == HEAVY_MODEL
    assert resolve_llm_model(
        IntentCategory.PLAY_MEDIA,
        {"platform": "local", "song": ""},
    ) == FAST_MODEL


def test_local_music_skips_voice_samples_and_finds_real_tracks():
    from executor.music_player import LOCAL_MUSIC_ROOTS, _find_local_track, _is_skipped_local_track

    assert _is_skipped_local_track("FRIDAY voice")
    assert _is_skipped_local_track("JARVIS - Marvel's Iron Man 3")
    assert _is_skipped_local_track("MCU_ F.R.I.D.A.Y. Lines (Not 100%)")
    assert _is_skipped_local_track("voice_preview_friday (1)")
    assert not _is_skipped_local_track("Counting Stars")

    audio_root = next((root for root in LOCAL_MUSIC_ROOTS if root.exists()), None)
    if audio_root is None:
        return

    track = _find_local_track("")
    assert track is not None
    assert not _is_skipped_local_track(track.stem)
    assert "local_music" in str(track).replace("\\", "/") or "Audio" in str(track) or "Music" in str(track)
