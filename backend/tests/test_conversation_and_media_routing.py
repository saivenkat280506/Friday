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


def test_local_music_never_becomes_a_spotify_search():
    assert parse_music_command("play music from local") == {
        "song": "",
        "platform": "local",
    }
