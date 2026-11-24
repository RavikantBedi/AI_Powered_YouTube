"""Voice Director and Voice Generation agents.

Narration is synthesized PER SCENE, not as one long file. That gives three things the
one-file approach cannot: the measured duration of each scene (which drives clip
lengths, so audio and picture are locked), per-segment regeneration when one segment
fails, and word-accurate subtitle timing.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

from app.agents.base import AgentContext, BaseAgent
from app.agents.registry import register
from app.core.costs import TTS_PRICE_PER_1K_CHARS, record_cost
from app.core.errors import ProviderUnavailableError
from app.providers import registry as provider_registry
from app.schemas.core import Scene, Storyboard, VoiceDirective
from app.services import ffmpeg

log = logging.getLogger("agency.voice")

MAX_CONCURRENT_TTS = 3

DIRECTOR_SYSTEM = """You are the Voice Director. Choose the narration performance for a
documentary from its script and the channel's narration DNA.

Return ONLY JSON:
{"tone": "...", "emotion": "...", "speed": 0.95, "style": "...",
 "gender": "male|female", "age": "...",
 "pronunciation_notes": [{"term": "...", "guidance": "how it must be pronounced"}],
 "reasoning": "one sentence"}

speed is a multiplier: 1.0 is natural pace. Grave documentary narration sits between
0.88 and 0.98. Never exceed 1.05 for this genre."""


class VoiceDirectorAgent(BaseAgent):
    agent_id = "voice-director-agent"
    name = "Voice Director Agent"
    version = 1
    capabilities = ["direct_voice"]
    task_kind = "voice_direction"
    stage = "VOICE_DIRECTION"

    async def execute(self, ctx: AgentContext, *, script_text: str,
                      language: str) -> VoiceDirective:
        user = (
            f"LANGUAGE: {language}\n"
            f"NARRATION DNA:\n"
            f"{json.dumps({k: v for k, v in ctx.script_dna.items() if k in {'narrator', 'language_register', 'sentence_rhythm'}}, ensure_ascii=False, indent=2)}\n\n"
            f"SCRIPT OPENING:\n{script_text[:3000]}\n\n"
            f"Direct the narration performance."
        )
        data = await self.llm_json(ctx, system=DIRECTOR_SYSTEM, user=user, max_tokens=2500,
                                   effort="low")
        directive = VoiceDirective(
            language=language,
            tone=data.get("tone", "serious"),
            speed=max(0.85, min(float(data.get("speed", 0.95)), 1.05)),
            emotion=data.get("emotion", "controlled"),
            style=data.get("style", "documentary"),
            voice_id=ctx.settings.elevenlabs_voice_id,
        )
        ctx.store.save_json("audio/voice_directive.json", directive)
        ctx.store.save_json("audio/pronunciation_notes.json",
                            data.get("pronunciation_notes", []))
        await ctx.emit("VOICE_DIRECTIVE_READY", {"tone": directive.tone,
                                                 "speed": directive.speed})
        return directive


class VoiceGenerationAgent(BaseAgent):
    agent_id = "voice-agent"
    name = "Voice Generation Agent"
    version = 1
    capabilities = ["synthesize_narration"]
    stage = "VOICE"

    async def execute(self, ctx: AgentContext, *, storyboard: Storyboard,
                      directive: VoiceDirective,
                      only_scenes: list[str] | None = None) -> Storyboard:
        targets = [s for s in storyboard.scenes
                   if s.narration_text and
                   ((only_scenes is None and not s.audio_path)
                    or (only_scenes is not None and s.scene_id in only_scenes))]
        if not targets:
            return storyboard

        semaphore = asyncio.Semaphore(MAX_CONCURRENT_TTS)
        done = 0
        failures: list[str] = []
        chars = 0

        async def speak(scene: Scene) -> None:
            nonlocal done, chars
            async with semaphore:
                out = ctx.store.path(f"audio/segments/{scene.scene_id}.mp3")
                text = clean_for_tts(scene.narration_text)
                used = {"name": ""}

                async def op(provider):
                    return await provider.synthesize(
                        text=text, language=directive.language, out_path=out,
                        voice_id=directive.voice_id, speed=directive.speed,
                        style=directive.style)
                try:
                    await provider_registry.call_with_fallback(
                        "tts", op, on_provider_used=lambda n: used.update(name=n))
                    scene.audio_path = str(out)
                    # Measured duration is the timeline's source of truth.
                    scene.duration_seconds = round(await ffmpeg.probe_duration(out), 3)
                    chars += len(text)
                    ctx.store.record_provenance(str(out), used["name"], "text-to-speech")
                except (ProviderUnavailableError, ffmpeg.RenderError) as e:
                    failures.append(scene.scene_id)
                    log.warning("TTS failed for %s: %s", scene.scene_id, e)
                finally:
                    done += 1
                    if done % 10 == 0 or done == len(targets):
                        await ctx.progress("VOICE", 100 * done / len(targets),
                                           f"{done}/{len(targets)} segments")

        await asyncio.gather(*(speak(s) for s in targets))

        if failures:
            # One retry pass for stragglers before declaring the stage failed.
            log.info("retrying %d failed TTS segments", len(failures))
            retry = [s for s in storyboard.scenes if s.scene_id in failures]
            failures.clear()
            await asyncio.gather(*(speak(s) for s in retry))

        # Re-derive the timeline from measured durations.
        clock = 0.0
        for scene in storyboard.scenes:
            scene.start_seconds = round(clock, 3)
            clock += scene.duration_seconds
        storyboard.total_duration = round(clock, 3)

        await record_cost(ctx.project_id, self.agent_id, "tts", units=chars,
                          cost_usd=(chars / 1000) * TTS_PRICE_PER_1K_CHARS)
        ctx.store.save_json("storyboard/storyboard.json", storyboard)

        rendered = sum(1 for s in storyboard.scenes if s.audio_path)
        await ctx.emit("VOICE_READY", {"segments": rendered,
                                       "duration": storyboard.total_duration,
                                       "failed": len(failures)})
        if rendered == 0:
            raise ProviderUnavailableError(
                "tts", "no narration segment could be synthesized",
                missing=provider_registry.missing_credentials("tts"))
        return storyboard


def clean_for_tts(text: str) -> str:
    """Strip anything a TTS engine would read aloud as noise."""
    text = re.sub(r"\[[^\]]*\]", " ", text)
    text = re.sub(r"[*_#`~<>|]", " ", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


register(VoiceDirectorAgent())
register(VoiceGenerationAgent())
