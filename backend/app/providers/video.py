"""Motion/video adapters.

KenBurnsVideoProvider is the built-in engine: it requires no external API, always
works when FFmpeg is present, and produces exactly the documentary motion vocabulary
the channel DNA asks for (slow push-in, gentle pan, controlled zoom).

KlingVideoProvider is a real image-to-video adapter for higher-fidelity motion; it is
tried first when credentialed and falls back to Ken Burns on any failure.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx

from app.config import get_settings
from app.core.errors import ProviderUnavailableError
from app.providers.base import VideoProvider
from app.services import ffmpeg

log = logging.getLogger("agency.video")


class KenBurnsVideoProvider(VideoProvider):
    name = "kenburns"

    def __init__(self) -> None:
        if not ffmpeg.ffmpeg_available():
            raise ProviderUnavailableError("kenburns", "ffmpeg/ffprobe not on PATH",
                                           missing=["ffmpeg", "ffprobe"])

    async def animate(self, *, image_path: Path, motion: str, duration_seconds: float,
                      out_path: Path) -> Path:
        return await ffmpeg.ken_burns_clip(image_path, out_path, duration_seconds, motion)


class KlingVideoProvider(VideoProvider):
    """Kling image-to-video via the PiAPI-compatible REST surface."""
    name = "kling"

    def __init__(self) -> None:
        s = get_settings()
        if not s.kling_api_key:
            raise ProviderUnavailableError("kling", "no API key", missing=["KLING_API_KEY"])
        self.api_key = s.kling_api_key

    async def animate(self, *, image_path: Path, motion: str, duration_seconds: float,
                      out_path: Path) -> Path:
        import base64

        payload = {
            "model": "kling",
            "task_type": "video_generation",
            "input": {
                "image": base64.b64encode(image_path.read_bytes()).decode(),
                "prompt": f"Subtle documentary camera motion: {motion}. No character movement.",
                "duration": 5,
                "aspect_ratio": "16:9",
                "mode": "std",
            },
        }
        headers = {"x-api-key": self.api_key, "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
            r = await client.post("https://api.piapi.ai/api/v1/task", json=payload, headers=headers)
            if r.status_code >= 400:
                raise ProviderUnavailableError("kling", f"HTTP {r.status_code}: {r.text[:200]}")
            task_id = r.json().get("data", {}).get("task_id")
            if not task_id:
                raise ProviderUnavailableError("kling", "no task_id in response")

            video_url = ""
            for _ in range(60):  # poll up to ~5 minutes
                await asyncio.sleep(5)
                poll = await client.get(f"https://api.piapi.ai/api/v1/task/{task_id}",
                                        headers=headers)
                if poll.status_code >= 400:
                    continue
                data = poll.json().get("data", {})
                status = data.get("status", "")
                if status == "completed":
                    video_url = (data.get("output") or {}).get("video_url", "")
                    break
                if status in {"failed", "cancelled"}:
                    raise ProviderUnavailableError("kling", f"task {status}")
            if not video_url:
                raise ProviderUnavailableError("kling", "generation timed out")

            raw = out_path.with_suffix(".raw.mp4")
            raw.parent.mkdir(parents=True, exist_ok=True)
            dl = await client.get(video_url, timeout=httpx.Timeout(300.0))
            dl.raise_for_status()
            raw.write_bytes(dl.content)

        await ffmpeg.normalize_clip(raw, out_path, duration_seconds)
        raw.unlink(missing_ok=True)
        return out_path
