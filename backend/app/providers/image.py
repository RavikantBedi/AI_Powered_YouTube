"""Image generation adapters. Three real providers, all behind ImageProvider.
Selection and fallback ordering live in the provider registry.

None of these fabricate an image when credentials are absent — they raise
ProviderUnavailableError so the registry can fall through or BLOCK the project.
"""
from __future__ import annotations

import base64
import logging
from pathlib import Path

import httpx

from app.config import get_settings
from app.core.errors import ProviderUnavailableError
from app.providers.base import ImageProvider

log = logging.getLogger("agency.image")

_ASPECT_SIZES = {"16:9": (1536, 864), "1:1": (1024, 1024), "9:16": (864, 1536)}
_TIMEOUT = httpx.Timeout(180.0, connect=15.0)


class GeminiImageProvider(ImageProvider):
    """Google Gemini image generation (Nano Banana family)."""
    name = "gemini"

    def __init__(self) -> None:
        s = get_settings()
        if not s.gemini_api_key:
            raise ProviderUnavailableError("gemini", "no API key", missing=["GEMINI_API_KEY"])
        self.api_key = s.gemini_api_key
        self.model = s.gemini_image_model

    async def generate(self, *, prompt: str, aspect_ratio: str = "16:9", out_path: Path,
                       reference_images: list[Path] | None = None, seed: int | None = None) -> Path:
        parts: list[dict] = [{"text": prompt}]
        for ref in (reference_images or [])[:3]:
            parts.append({"inline_data": {
                "mime_type": "image/png",
                "data": base64.b64encode(Path(ref).read_bytes()).decode(),
            }})
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{self.model}:generateContent")
        body = {
            "contents": [{"parts": parts}],
            "generationConfig": {"imageConfig": {"aspectRatio": aspect_ratio}},
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(url, json=body, headers={"x-goog-api-key": self.api_key})
        if r.status_code >= 500 or r.status_code == 429:
            raise ProviderUnavailableError("gemini", f"HTTP {r.status_code}: {r.text[:200]}")
        r.raise_for_status()
        data = r.json()
        for cand in data.get("candidates", []):
            for part in cand.get("content", {}).get("parts", []):
                blob = part.get("inlineData") or part.get("inline_data")
                if blob and blob.get("data"):
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_path.write_bytes(base64.b64decode(blob["data"]))
                    return out_path
        raise ProviderUnavailableError("gemini", f"no image in response: {str(data)[:300]}")


class OpenAIImageProvider(ImageProvider):
    name = "openai"

    def __init__(self) -> None:
        s = get_settings()
        if not s.openai_api_key:
            raise ProviderUnavailableError("openai", "no API key", missing=["OPENAI_API_KEY"])
        self.api_key = s.openai_api_key
        self.model = s.openai_image_model

    async def generate(self, *, prompt: str, aspect_ratio: str = "16:9", out_path: Path,
                       reference_images: list[Path] | None = None, seed: int | None = None) -> Path:
        size = {"16:9": "1536x1024", "1:1": "1024x1024", "9:16": "1024x1536"}.get(aspect_ratio, "1536x1024")
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(
                "https://api.openai.com/v1/images/generations",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "prompt": prompt, "size": size, "n": 1},
            )
        if r.status_code >= 500 or r.status_code == 429:
            raise ProviderUnavailableError("openai", f"HTTP {r.status_code}: {r.text[:200]}")
        r.raise_for_status()
        item = r.json()["data"][0]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if item.get("b64_json"):
            out_path.write_bytes(base64.b64decode(item["b64_json"]))
        else:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                img = await client.get(item["url"])
            img.raise_for_status()
            out_path.write_bytes(img.content)
        return out_path


class StabilityImageProvider(ImageProvider):
    name = "stability"

    def __init__(self) -> None:
        s = get_settings()
        if not s.stability_api_key:
            raise ProviderUnavailableError("stability", "no API key", missing=["STABILITY_API_KEY"])
        self.api_key = s.stability_api_key

    async def generate(self, *, prompt: str, aspect_ratio: str = "16:9", out_path: Path,
                       reference_images: list[Path] | None = None, seed: int | None = None) -> Path:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(
                "https://api.stability.ai/v2beta/stable-image/generate/core",
                headers={"Authorization": f"Bearer {self.api_key}", "Accept": "image/*"},
                files={"none": ""},
                data={"prompt": prompt, "aspect_ratio": aspect_ratio, "output_format": "png",
                      **({"seed": str(seed)} if seed is not None else {})},
            )
        if r.status_code >= 500 or r.status_code == 429:
            raise ProviderUnavailableError("stability", f"HTTP {r.status_code}: {r.text[:200]}")
        r.raise_for_status()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(r.content)
        return out_path
