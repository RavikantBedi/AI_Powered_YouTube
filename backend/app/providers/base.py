"""Provider interfaces. Agents depend only on these ABCs; concrete adapters are
selected by the provider registry with fallback chains. A provider that cannot run
raises ProviderUnavailableError — it never fakes output."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

# ── LLM ─────────────────────────────────────────────────────────────────

@dataclass
class LLMResult:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    stop_reason: str = ""
    citations: list[dict] = field(default_factory=list)


class LLMProvider(ABC):
    name: str = "llm"

    @abstractmethod
    async def complete(self, *, model: str, system: str, user: str,
                       max_tokens: int = 16000, effort: str = "high") -> LLMResult: ...

    @abstractmethod
    async def complete_with_web_search(self, *, model: str, system: str, user: str,
                                       max_tokens: int = 16000, max_searches: int = 12) -> LLMResult: ...

    @abstractmethod
    async def complete_with_images(self, *, model: str, system: str, user: str,
                                   images: list[Path], max_tokens: int = 8000,
                                   effort: str = "high") -> LLMResult: ...


# ── Media generation ────────────────────────────────────────────────────

class ImageProvider(ABC):
    name: str = "image"

    @abstractmethod
    async def generate(self, *, prompt: str, aspect_ratio: str = "16:9",
                       out_path: Path, reference_images: list[Path] | None = None,
                       seed: int | None = None) -> Path: ...


class TTSProvider(ABC):
    name: str = "tts"

    @abstractmethod
    async def synthesize(self, *, text: str, language: str, out_path: Path,
                         voice_id: str = "", speed: float = 1.0, style: str = "") -> Path: ...


class VideoProvider(ABC):
    """Image-to-video motion. The FFmpeg Ken-Burns engine is the always-available fallback."""
    name: str = "video"

    @abstractmethod
    async def animate(self, *, image_path: Path, motion: str, duration_seconds: float,
                      out_path: Path) -> Path: ...


class MusicProvider(ABC):
    name: str = "music"

    @abstractmethod
    async def select(self, *, mood: str, duration_seconds: float) -> Path | None: ...


# ── Platform ────────────────────────────────────────────────────────────

class YouTubeProvider(ABC):
    name: str = "youtube"

    @abstractmethod
    async def upload(self, *, video_path: Path, title: str, description: str, tags: list[str],
                     category_id: str, language: str, privacy_status: str,
                     thumbnail_path: Path | None = None,
                     captions: list[tuple[Path, str]] | None = None) -> dict: ...


class NotificationProvider(ABC):
    name: str = "notify"

    @abstractmethod
    async def send(self, *, title: str, body: str, url: str = "") -> bool: ...


class SearchProvider(ABC):
    name: str = "search"

    @abstractmethod
    async def search(self, query: str, max_results: int = 8) -> list[dict]: ...
