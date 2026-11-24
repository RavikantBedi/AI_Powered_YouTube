"""Application configuration. All secrets come from the environment / .env — never source."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    # Core
    agency_api_key: str = "change-me"
    database_url: str = "sqlite+aiosqlite:///./agency.db"
    artifact_root: str = "./artifacts"
    cors_origins: str = "http://localhost:3000"
    channel_dna_root: str = "../channel_dna"
    generated_agents_dir: str = "./generated_agents"

    # LLM
    anthropic_api_key: str = ""
    model_heavy: str = "claude-opus-5"
    model_standard: str = "claude-sonnet-5"
    model_light: str = "claude-haiku-4-5"

    # Images
    gemini_api_key: str = ""
    gemini_image_model: str = "gemini-2.5-flash-image"
    openai_api_key: str = ""
    openai_image_model: str = "gpt-image-1"
    stability_api_key: str = ""

    # Voice
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""
    elevenlabs_model: str = "eleven_multilingual_v2"
    edge_tts_voice: str = "hi-IN-MadhurNeural"

    # Motion
    kling_api_key: str = ""
    runway_api_key: str = ""

    # Search
    tavily_api_key: str = ""

    # YouTube
    youtube_client_secret_file: str = "./secrets/client_secret.json"
    youtube_token_file: str = "./secrets/youtube_token.json"
    youtube_default_privacy: str = "private"

    # Notifications
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    notify_email_to: str = ""
    notify_webhook_url: str = ""

    # Music
    music_library_dir: str = "./assets/music"

    # Quality gates
    gate_research: int = 90
    gate_fact_check: int = 95
    gate_script: int = 90
    gate_visual: int = 88
    gate_voice: int = 90
    gate_edit: int = 90
    gate_thumbnail: int = 88
    gate_seo: int = 85
    gate_final: int = 92
    max_revision_loops: int = 3

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def artifact_root_path(self) -> Path:
        p = Path(self.artifact_root)
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache
def get_settings() -> Settings:
    return Settings()
