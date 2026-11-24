"""Image Generation Agent — renders every scene, in bounded parallel, through the
provider chain with automatic fallback.

Character reference images are threaded through: the first successfully generated image
featuring a character becomes that character's visual anchor and is passed as a
reference to later scenes, which keeps appearance stable across a 150-scene film.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.agents.base import AgentContext, BaseAgent
from app.agents.registry import register
from app.core.costs import IMAGE_PRICE_DEFAULT, record_cost
from app.core.errors import ProviderUnavailableError
from app.providers import registry as provider_registry
from app.schemas.core import Scene, Storyboard

log = logging.getLogger("agency.image")

MAX_CONCURRENT = 4


class ImageGenerationAgent(BaseAgent):
    agent_id = "image-agent"
    name = "Image Generation Agent"
    version = 1
    capabilities = ["generate_images", "render_scene"]
    stage = "IMAGES"

    async def execute(self, ctx: AgentContext, *, storyboard: Storyboard,
                      only_scenes: list[str] | None = None) -> Storyboard:
        targets = [s for s in storyboard.scenes
                   if (only_scenes is None and not s.image_path) or
                   (only_scenes is not None and s.scene_id in only_scenes)]
        if not targets:
            return storyboard

        # Character anchors: scene_id -> reference image, keyed by character.
        anchors: dict[str, Path] = {}
        for s in storyboard.scenes:
            if s.image_path and s.qa_score >= 80:
                for cid in s.characters:
                    anchors.setdefault(cid, Path(s.image_path))

        semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        done = 0
        failures: list[tuple[str, str]] = []
        lock = asyncio.Lock()

        async def render(scene: Scene) -> None:
            nonlocal done
            async with semaphore:
                refs = [anchors[c] for c in scene.characters if c in anchors][:2]
                out = ctx.store.path(f"visuals/{scene.scene_id}.png")
                try:
                    used_provider = {"name": ""}

                    async def op(provider):
                        return await provider.generate(
                            prompt=scene.image_prompt,
                            aspect_ratio=ctx.visual_dna.get("aspect_ratio", "16:9"),
                            out_path=out, reference_images=refs)

                    await provider_registry.call_with_fallback(
                        "image", op, on_provider_used=lambda n: used_provider.update(name=n))
                    scene.image_path = str(out)
                    ctx.store.record_provenance(str(out), used_provider["name"],
                                                "text-to-image generation")
                    await record_cost(ctx.project_id, self.agent_id, "image",
                                      provider=used_provider["name"], units=1,
                                      cost_usd=IMAGE_PRICE_DEFAULT)
                    async with lock:
                        for cid in scene.characters:
                            anchors.setdefault(cid, out)
                except ProviderUnavailableError as e:
                    failures.append((scene.scene_id, str(e)))
                    log.warning("scene %s failed: %s", scene.scene_id, e)
                finally:
                    async with lock:
                        done += 1
                    if done % 5 == 0 or done == len(targets):
                        await ctx.progress("IMAGES", 100 * done / len(targets),
                                           f"{done}/{len(targets)} scenes")

        # Render the first few sequentially so character anchors exist before the fan-out.
        for scene in targets[:2]:
            await render(scene)
        await asyncio.gather(*(render(s) for s in targets[2:]))

        ctx.store.save_json("storyboard/storyboard.json", storyboard)
        rendered = sum(1 for s in storyboard.scenes if s.image_path)
        await ctx.emit("IMAGES_READY", {"rendered": rendered, "total": len(storyboard.scenes),
                                        "failed": len(failures)})

        if rendered == 0:
            raise ProviderUnavailableError(
                "image", "every scene failed to render",
                missing=provider_registry.missing_credentials("image"))
        return storyboard


register(ImageGenerationAgent())
