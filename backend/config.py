import os

from pydantic_settings import BaseSettings, SettingsConfigDict

from paths import BACKEND_DIR, PROJECT_ROOT

_ROOT_ENV = os.path.join(PROJECT_ROOT, ".env")
_BACKEND_ENV = os.path.join(BACKEND_DIR, ".env")
_DOT_ENV = _ROOT_ENV if os.path.isfile(_ROOT_ENV) else _BACKEND_ENV


class Settings(BaseSettings):
    # API Keys
    GROQ_API_KEY: str = ""
    NEWS_API_KEY: str = ""
    STT_API_KEY: str = ""
    HF_TOKEN: str = ""

    # LLM provider: "groq" (cloud) or "ollama" (local)
    LLM_PROVIDER: str = "groq"
    OLLAMA_URL: str = "http://127.0.0.1:11434"
    OLLAMA_MODEL: str = "qwen3.5:4b"
    # Keep the local model loaded; smaller context = faster prefill on 16 GB.
    OLLAMA_KEEP_ALIVE: str = "-1"
    OLLAMA_NUM_CTX: int = 2048

    # Model settings — fast default; heavy used for search/headlines/planning
    LLM_MODEL: str = "openai/gpt-oss-20b"
    LLM_MODEL_HEAVY: str = "openai/gpt-oss-120b"

    # TTS — pocket_tts only, cloned from friday-voice.wav (see FRIDAY_VOICE_PATH).
    CLOUD_TTS_PROVIDER: str = "pocket"
    CLOUD_TTS_ENDPOINT: str = ""
    FRIDAY_VOICE_PATH: str = ""

    # STT — local Faster-Whisper is unlimited and keeps microphone audio on-device.
    # `medium.en` is the accuracy-first default; use `small.en` on lower-spec CPUs.
    STT_MODEL: str = "medium.en"
    STT_DEVICE: str = "cpu"
    STT_COMPUTE_TYPE: str = "int8"
    # Input device index for sounddevice (-1 = system default). Set in .env if mic is wrong.
    STT_INPUT_DEVICE: int = -1
    STT_VAD_MODE: int = 1
    STT_SPEECH_RMS: int = 55
    # Seconds of silence after you stop speaking before the mic closes (~1s default).
    STT_SILENCE_TIMEOUT_S: float = 1.25
    # How long to keep the mic open waiting for the first word (companion hotkey).
    STT_PRE_SPEECH_TIMEOUT_S: float = 15.0
    # Speech RMS must exceed ambient noise floor by this factor (rejects TV/room chatter).
    STT_SPEECH_SNR_MULT: float = 1.6
    # Fast on-device model for live companion partials (final uses Groq or STT_MODEL).
    STT_PARTIAL_MODEL: str = "base.en"
    # auto = Groq Whisper when GROQ_API_KEY is set, else local Faster-Whisper
    STT_PROVIDER: str = "auto"

    # Browser agent (Puppeteer sidecar)
    BROWSER_AGENT_PORT: int = 9477
    BROWSER_AGENT_URL: str = ""
    BROWSER_AGENT_DEFAULT_MODE: str = "headed"
    CHROME_USER_DATA_DIR: str = ""
    CHROME_PROFILE: str = "Default"

    model_config = SettingsConfigDict(
        env_file=_DOT_ENV,
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()


def groq_api_key() -> str:
    """Groq rejects empty keys at client init; callers still fail later if unset."""
    key = (settings.GROQ_API_KEY or os.environ.get("GROQ_API_KEY") or "").strip()
    return key or "not-configured"


def llm_provider() -> str:
    return (settings.LLM_PROVIDER or "groq").strip().lower()


def use_ollama() -> bool:
    """True while FRIDAY is running on the local Ollama model."""
    return llm_provider() == "ollama"


def active_llm_model() -> str:
    if use_ollama():
        return (settings.OLLAMA_MODEL or settings.LLM_MODEL or "qwen3.5:4b").strip()
    return (settings.LLM_MODEL or "openai/gpt-oss-20b").strip()


def ollama_base_url() -> str:
    return (settings.OLLAMA_URL or os.environ.get("OLLAMA_URL") or "http://127.0.0.1:11434").rstrip("/")
