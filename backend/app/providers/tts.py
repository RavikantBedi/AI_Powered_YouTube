"""Text-to-speech adapters.

ElevenLabs is preferred for Hindi documentary narration quality; edge-tts is a
genuinely free fallback that requires no credentials, so the pipeline can produce a
real voiceover with only an Anthropic key configured.
"""
from __future__ import annotations

import logging
from pathlib import Path

import httpx

from app.config import get_settings
from app.core.errors import ProviderUnavailableError
from app.providers.base import TTSProvider

log = logging.getLogger("agency.tts")


class ElevenLabsTTS(TTSProvider):
    name = "elevenlabs"

    def __init__(self) -> None:
        s = get_settings()
        if not s.elevenlabs_api_key:
            raise ProviderUnavailableError("elevenlabs", "no API key", missing=["ELEVENLABS_API_KEY"])
        self.api_key = s.elevenlabs_api_key
        self.default_voice = s.elevenlabs_voice_id or "onwK4e9ZLuTAKqWW03F9"  # Daniel — grave male
        self.model = s.elevenlabs_model

    async def synthesize(self, *, text: str, language: str, out_path: Path,
                         voice_id: str = "", speed: float = 1.0, style: str = "") -> Path:
        voice = voice_id or self.default_voice
        body = {
            "text": text,
            "model_id": self.model,
            "voice_settings": {
                "stability": 0.55,          # documentary: steady, low variance
                "similarity_boost": 0.75,
                "style": 0.25,
                "use_speaker_boost": True,
                "speed": max(0.7, min(1.2, speed)),
            },
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0)) as client:
            r = await client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
                headers={"xi-api-key": self.api_key, "Accept": "audio/mpeg"},
                json=body,
            )
        if r.status_code == 429 or r.status_code >= 500:
            raise ProviderUnavailableError("elevenlabs", f"HTTP {r.status_code}: {r.text[:200]}")
        if r.status_code == 401:
            raise ProviderUnavailableError("elevenlabs", "unauthorized", missing=["ELEVENLABS_API_KEY"])
        r.raise_for_status()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(r.content)
        return out_path


class EdgeTTS(TTSProvider):
    """Free Microsoft Edge neural voices. No credentials required."""
    name = "edge-tts"

    def __init__(self) -> None:
        try:
            import edge_tts  # noqa: F401
        except ImportError as e:
            raise ProviderUnavailableError("edge-tts", "package not installed",
                                           missing=["pip install edge-tts"]) from e
        self.voice = get_settings().edge_tts_voice

    async def synthesize(self, *, text: str, language: str, out_path: Path,
                         voice_id: str = "", speed: float = 1.0, style: str = "") -> Path:
        import edge_tts

        voice = voice_id or self.voice
        if not language.lower().startswith("hi") and voice.startswith("hi-"):
            voice = "en-US-GuyNeural"
        # edge-tts rate is a percentage delta from the default speaking rate.
        rate = f"{round((speed - 1.0) * 100):+d}%"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            communicate = edge_tts.Communicate(text, voice, rate=rate)
            await communicate.save(str(out_path))
        except Exception as e:
            raise ProviderUnavailableError("edge-tts", f"synthesis failed: {e}") from e
        if not out_path.exists() or out_path.stat().st_size == 0:
            raise ProviderUnavailableError("edge-tts", "produced empty audio")
        return out_path
