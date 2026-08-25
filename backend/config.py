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

    # Model settings — fast default; heavy used for search/headlines/planning
    LLM_MODEL: str = "llama-3.1-8b-instant"
    LLM_MODEL_HEAVY: str = "llama-3.3-70b-versatile"

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
    STT_PARTIAL_MODEL: str = "tiny.en"
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
