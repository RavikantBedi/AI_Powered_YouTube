"""Storyboard, character consistency, and visual direction.

Scene decomposition is deterministic: the approved script is split at sentence
boundaries into narration chunks sized for 3-6 seconds of speech. Each scene owns its
narration text, which is what gets sent to TTS — so audio and visuals are synchronized
by construction, and the timeline never drifts.

Three agents live here because they operate on the same object:
  StoryboardAgent      script  -> scenes
  CharacterDesignAgent script  -> persistent character identities
  VisualDirectorAgent  scenes  -> standalone, DNA-compliant image prompts
"""
from __future__ import annotations

import json
import logging
import re

from app.agents.base import AgentContext, BaseAgent
from app.agents.registry import register
from app.schemas.core import CharacterProfile, ResearchDossier, Scene, Script, Storyboard

log = logging.getLogger("agency.storyboard")

MIN_SCENE_SECONDS = 3.0
MAX_SCENE_SECONDS = 6.0


def split_sentences(text: str) -> list[str]:
    """Split Hindi/English narration on danda and western terminators."""
    text = text.replace("[TENSION PEAK]", " [TENSION PEAK] ")
    parts = re.split(r"(?<=[।?!])\s+|(?<=[.!?])\s+(?=[A-Zऀ-ॿ])", text)
    return [p.strip() for p in parts if p.strip()]


# ── Scene decomposition ─────────────────────────────────────────────────

class StoryboardAgent(BaseAgent):
    agent_id = "storyboard-agent"
    name = "Storyboard Agent"
    version = 1
    capabilities = ["decompose_script", "build_storyboard"]
    task_kind = "storyboard"
    stage = "STORYBOARD"

    async def execute(self, ctx: AgentContext, *, script: Script, wpm: int = 130) -> Storyboard:
        sentences = split_sentences(script.text)
        words_per_second = wpm / 60.0
        min_words = int(MIN_SCENE_SECONDS * words_per_second)
        max_words = int(MAX_SCENE_SECONDS * words_per_second)

        scenes: list[Scene] = []
        buffer: list[str] = []
        buffer_words = 0
        tension = False

        def flush() -> None:
            nonlocal buffer, buffer_words, tension
            if not buffer:
                return
            narration = " ".join(buffer)
            is_peak = "[TENSION PEAK]" in narration or tension
            narration = narration.replace("[TENSION PEAK]", "").strip()
            narration = re.sub(r"\s{2,}", " ", narration)
            if narration:
                idx = len(scenes)
                scenes.append(Scene(
                    scene_id=f"S{idx + 1:04d}",
                    index=idx,
                    narration_text=narration,
                    duration_seconds=round(max(MIN_SCENE_SECONDS,
                                               len(narration.split()) / words_per_second), 2),
                    mood="tense" if is_peak else "",
                ))
            buffer, buffer_words, tension = [], 0, False

        for sentence in sentences:
            if sentence == "[TENSION PEAK]":
                tension = True
                continue
            n = len(sentence.split())
            if buffer_words + n > max_words and buffer_words >= min_words:
                flush()
            buffer.append(sentence)
            buffer_words += n
            if buffer_words >= max_words:
                flush()
        flush()

        # Estimated start times; replaced with measured audio durations after TTS.
        clock = 0.0
        for scene in scenes:
            scene.start_seconds = round(clock, 2)
            clock += scene.duration_seconds

        board = Storyboard(scenes=scenes, total_duration=round(clock, 2))
        ctx.store.save_json("storyboard/storyboard.json", board)
        await ctx.emit("STORYBOARD_READY", {"scenes": len(scenes),
                                            "estimated_duration": board.total_duration})
        return board


# ── Character consistency ───────────────────────────────────────────────

CHARACTER_SYSTEM = """You are the Character Design Agent for a 2D documentary animation
studio. You lock down how each recurring real person is drawn so their appearance never
drifts between scenes.

For every significant person in the script, produce a canonical visual identity. The
`prompt_fragment` is the exact wording that will be pasted into every image prompt
featuring them — it must be self-contained, specific, and repeatable: age, build, face
shape, complexion, facial hair, hairstyle, habitual clothing of the correct era, and any
signature accessory.

CRITICAL POLICY:
- Historical or deceased public figures: describe their actual documented likeness.
- Currently serving heads of state or serving senior officials: set
  is_serving_head_of_state true, and write a prompt_fragment that uses ONLY symbolic
  representation — a silhouette, a figure seen from behind, an empty official chair, a
  nameplate, a podium — never facial features. Do not describe their face at all.

Return ONLY JSON:
{"characters": [{"name": "...", "is_serving_head_of_state": false, "age_appearance": "...",
  "appearance": "...", "clothing": "...", "facial_features": "...", "hairstyle": "...",
  "palette": "...", "prompt_fragment": "..."}]}"""


class CharacterDesignAgent(BaseAgent):
    agent_id = "character-agent"
    name = "Character Consistency Agent"
    version = 1
    capabilities = ["design_characters", "maintain_consistency"]
    task_kind = "characters"
    stage = "CHARACTERS"

    async def execute(self, ctx: AgentContext, *, script: Script,
                      dossier: ResearchDossier) -> list[CharacterProfile]:
        people = json.dumps(dossier.people, ensure_ascii=False)[:6000]
        user = (
            f"TOPIC: {dossier.topic}\n\n"
            f"PEOPLE IDENTIFIED IN RESEARCH (note the deceased / currently_serving_official "
            f"flags):\n{people}\n\n"
            f"VISUAL STYLE DNA:\n{json.dumps(ctx.visual_dna, ensure_ascii=False, indent=2)}\n\n"
            f"REAL-FIGURE POLICY:\n"
            f"{json.dumps(ctx.channel_dna.get('real_figures_policy', {}), ensure_ascii=False)}\n\n"
            f"SCRIPT (identify who actually appears):\n{script.text[:20000]}\n\n"
            f"Produce canonical identities for every person who appears more than once."
        )
        data = await self.llm_json(ctx, system=CHARACTER_SYSTEM, user=user, max_tokens=12000)

        profiles: list[CharacterProfile] = []
        for i, c in enumerate(data.get("characters", [])):
            serving = bool(c.get("is_serving_head_of_state", False))
            fragment = c.get("prompt_fragment", "")
            if serving:
                # Enforce the policy in code, not just in the prompt.
                fragment = (f"a symbolic representation of {c.get('name', 'the official')} — "
                            f"shown only as a silhouette from behind or an empty official chair "
                            f"with a nameplate, face never visible, no facial features rendered")
            profiles.append(CharacterProfile(
                character_id=f"CH{i + 1:03d}",
                name=c.get("name", ""),
                is_serving_head_of_state=serving,
                age_appearance=c.get("age_appearance", ""),
                appearance=c.get("appearance", ""),
                clothing=c.get("clothing", ""),
                facial_features="" if serving else c.get("facial_features", ""),
                hairstyle="" if serving else c.get("hairstyle", ""),
                palette=c.get("palette", ""),
                prompt_fragment=fragment,
            ))

        ctx.store.save_json("storyboard/characters.json", [p.model_dump() for p in profiles])
        await ctx.emit("CHARACTERS_READY", {"count": len(profiles),
                                            "symbolic": sum(p.is_serving_head_of_state for p in profiles)})
        return profiles


# ── Visual direction ────────────────────────────────────────────────────

VISUAL_SYSTEM = """You are the Visual Director for a 2D documentary animation studio.
You convert narration beats into image-generation prompts.

EVERY prompt must be a SINGLE continuous paragraph with no line breaks, and must
independently describe, with zero reliance on any other prompt:
(1) the subject/characters with physical detail and era-correct clothing
(2) the environment/setting with era-accurate props
(3) the lighting setup
(4) the mood/atmosphere
(5) the camera angle
(6) the visual style, stated explicitly

Then close with exactly this structure:
Camera: [angle]. Lighting: [setup]. Mood: [emotion]. Action: [what is happening].
[STYLE_SUFFIX]

HARD RULES:
- Never put narration text, subtitles, captions, letters, or any readable words INTO the
  image. Documents and maps appear as illustrated props without legible text.
- When a listed character appears, reproduce their canonical prompt_fragment verbatim so
  their appearance stays identical across scenes.
- For any character flagged as a serving head of state, use only the symbolic wording
  supplied — never describe their face.
- Historical reconstruction only: no fantasy, no anachronisms, no modern items in
  period scenes.
- Depict tension through weather, light, posture, and composition — never gore, never
  graphic injury.
- Vary camera angle and shot scale between consecutive scenes; do not repeat the same
  framing twice in a row.

Return ONLY JSON: {"prompts": [{"scene_id": "S0001", "prompt": "...",
"characters": ["CH001"], "motion": "slow push-in", "mood": "grave"}]}
`motion` must be one of the allowed motions in the supplied visual DNA."""


class VisualDirectorAgent(BaseAgent):
    agent_id = "visual-director-agent"
    name = "Visual Director Agent"
    version = 1
    capabilities = ["write_image_prompts", "direct_visuals"]
    task_kind = "visual_direction"
    stage = "VISUAL_DIRECTION"

    BATCH = 20  # matches the channel's documented batch size

    async def execute(self, ctx: AgentContext, *, storyboard: Storyboard,
                      characters: list[CharacterProfile], topic: str) -> Storyboard:
        visual = ctx.visual_dna
        style_suffix = visual.get("style_suffix",
                                  "Style: Semi-realistic 2D illustration, clean outlines, "
                                  "documentary style, 16:9 aspect ratio.")
        char_book = "\n".join(
            f"{c.character_id} — {c.name}"
            + (" [SERVING OFFICIAL: symbolic only]" if c.is_serving_head_of_state else "")
            + f": {c.prompt_fragment}"
            for c in characters) or "(no recurring characters identified)"

        scenes = storyboard.scenes
        batches = [scenes[i:i + self.BATCH] for i in range(0, len(scenes), self.BATCH)]
        by_id = {s.scene_id: s for s in scenes}

        for bi, batch in enumerate(batches):
            await ctx.progress("VISUAL_DIRECTION", 100 * bi / max(len(batches), 1),
                               f"batch {bi + 1}/{len(batches)}")
            listing = "\n".join(
                f"{s.scene_id} ({s.duration_seconds:.1f}s"
                + (", TENSION PEAK" if s.mood == "tense" else "") + f"): {s.narration_text}"
                for s in batch)
            user = (
                f"TOPIC: {topic}\n\n"
                f"VISUAL DNA:\n{json.dumps(visual, ensure_ascii=False, indent=2)}\n\n"
                f"STYLE_SUFFIX to append to every prompt, verbatim:\n{style_suffix}\n\n"
                f"CHARACTER BOOK (reproduce these fragments verbatim when the character "
                f"appears):\n{char_book}\n\n"
                f"NARRATION BEATS TO VISUALISE (one prompt each, in order):\n{listing}\n\n"
                f"The narration is the meaning of the shot — do not render its words as text. "
                f"Produce exactly {len(batch)} prompts."
            )
            data = await self.llm_json(ctx, system=VISUAL_SYSTEM, user=user, max_tokens=24000)

            allowed_motion = visual.get("motion", {}).get(
                "allowed", ["slow push-in", "gentle pan", "controlled zoom"])
            for i, p in enumerate(data.get("prompts", [])):
                scene = by_id.get(p.get("scene_id", "")) or (batch[i] if i < len(batch) else None)
                if scene is None:
                    continue
                prompt = " ".join(str(p.get("prompt", "")).split())  # force single paragraph
                if style_suffix.lower() not in prompt.lower():
                    prompt = f"{prompt.rstrip('. ')}. {style_suffix}"
                if "16:9" not in prompt:
                    prompt += " 16:9 aspect ratio."
                scene.image_prompt = prompt
                scene.characters = p.get("characters", [])
                motion = p.get("motion", "")
                scene.motion = motion if motion in allowed_motion else allowed_motion[0]
                scene.mood = p.get("mood", scene.mood)

        missing = [s.scene_id for s in scenes if not s.image_prompt]
        if missing:
            log.warning("%d scenes without prompts, filling from narration", len(missing))
            for s in scenes:
                if not s.image_prompt:
                    s.image_prompt = (
                        f"A grave documentary scene illustrating: {s.narration_text[:200]}. "
                        f"Camera: medium shot. Lighting: single-source dramatic side light. "
                        f"Mood: grave. Action: static documentary moment. {style_suffix}")

        ctx.store.save_json("storyboard/storyboard.json", storyboard)
        ctx.store.save_json("storyboard/image_prompts.json",
                            [{"scene_id": s.scene_id, "prompt": s.image_prompt,
                              "motion": s.motion} for s in scenes])
        await ctx.emit("VISUAL_DIRECTION_READY", {"prompts": len(scenes)})
        return storyboard


register(StoryboardAgent())
register(CharacterDesignAgent())
register(VisualDirectorAgent())
