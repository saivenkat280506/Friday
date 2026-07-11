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

    # Model settings
    LLM_MODEL: str = "llama-3.1-8b-instant"

    # TTS — pocket_tts only, cloned from friday-voice.wav (see FRIDAY_VOICE_PATH).
    CLOUD_TTS_PROVIDER: str = "pocket"
    CLOUD_TTS_ENDPOINT: str = ""
    FRIDAY_VOICE_PATH: str = ""

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