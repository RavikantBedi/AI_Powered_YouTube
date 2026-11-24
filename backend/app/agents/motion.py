"""Motion Generation Agent — turns approved stills into clips.

Clip duration is taken from the scene's MEASURED narration audio, not an estimate, so
picture and voice stay locked together with no drift across a 15-minute film.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.agents.base import AgentContext, BaseAgent
from app.agents.registry import register
from app.core.errors import ProviderUnavailableError
from app.providers import registry as provider_registry
from app.schemas.core import Scene, Storyboard

log = logging.getLogger("agency.motion")

MAX_CONCURRENT = 3


class MotionAgent(BaseAgent):
    agent_id = "motion-agent"
    name = "Motion / Video Generation Agent"
    version = 1
    capabilities = ["animate_scene", "generate_motion"]
    stage = "MOTION"

    async def execute(self, ctx: AgentContext, *, storyboard: Storyboard,
                      only_scenes: list[str] | None = None) -> Storyboard:
        targets = [s for s in storyboard.scenes
                   if s.image_path and ((only_scenes is None and not s.clip_path)
                                        or (only_scenes is not None and s.scene_id in only_scenes))]
        if not targets:
            return storyboard

        semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        done = 0
        failures: list[str] = []

        async def animate(scene: Scene) -> None:
            nonlocal done
            async with semaphore:
                out = ctx.store.path(f"visuals/{scene.scene_id}.mp4")
                used = {"name": ""}

                async def op(provider):
                    return await provider.animate(
                        image_path=Path(scene.image_path), motion=scene.motion,
                        duration_seconds=scene.duration_seconds, out_path=out)
                try:
                    await provider_registry.call_with_fallback(
                        "video", op, attempts_per_provider=2,
                        on_provider_used=lambda n: used.update(name=n))
                    scene.clip_path = str(out)
                    ctx.store.record_provenance(str(out), used["name"], "image-to-video motion")
                except ProviderUnavailableError as e:
                    failures.append(scene.scene_id)
                    log.warning("motion failed for %s: %s", scene.scene_id, e)
                finally:
                    done += 1
                    if done % 5 == 0 or done == len(targets):
                        await ctx.progress("MOTION", 100 * done / len(targets),
                                           f"{done}/{len(targets)} clips")

        await asyncio.gather(*(animate(s) for s in targets))

        ctx.store.save_json("storyboard/storyboard.json", storyboard)
        animated = sum(1 for s in storyboard.scenes if s.clip_path)
        await ctx.emit("MOTION_READY", {"clips": animated, "failed": len(failures)})

        if animated == 0:
            raise ProviderUnavailableError(
                "video", "no scene could be animated",
                missing=provider_registry.missing_credentials("video") or ["ffmpeg"])
        return storyboard


register(MotionAgent())
