from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM provider API keys
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    google_api_key: str = ""
    google_aistudio_api_key: str = ""  # alias used in some .env setups
    mistral_api_key: str = ""

    # TTS provider API keys
    eleven_labs_api_key: str = ""
    tts_enabled: bool = False
    tts_streaming_model: str = "eleven_flash_v2_5"

    # Local LiteLLM router (airlock). Leave empty to call providers directly.
    litellm_router_url: str = ""
    airlock_client: str = "one-0-one"

    # Filesystem paths
    sessions_path: Path = Path("./sessions")
    logs_path: Path = Path("./logs")
    session_templates_path: Path = Path("./session-templates")

    # Transcript checkpoint frequency (events between flushes)
    transcript_checkpoint_interval: int = 10

    # Logging
    log_level: str = "INFO"


# Module-level singleton — loaded once at import time
settings = Settings()
