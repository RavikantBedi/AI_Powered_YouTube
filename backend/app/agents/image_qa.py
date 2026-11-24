"""Image Quality Agent — actually looks at every generated frame.

Runs deterministic checks first (file integrity, aspect ratio) because they are free and
certain, then a vision pass in small batches. Scenes below threshold are regenerated with
a repaired prompt; persistent failures escalate to the Visual Repair path, which
simplifies the composition rather than retrying the same prompt forever.
"""
from __future__ import annotations

import logging
import struct
from pathlib import Path

from app.agents.base import AgentContext, BaseAgent
from app.agents.registry import register
from app.schemas.core import Scene, Storyboard

log = logging.getLogger("agency.image_qa")

BATCH = 6

SYSTEM = """You are the Image Quality Agent for a 2D documentary animation studio. You
inspect generated frames against the prompt that produced them and the channel's visual
style.

Score each image 0-100 on:
- prompt_adherence: does it show what the prompt asked for?
- style_consistency: semi-realistic 2D illustration, clean outlines, warm muted palette,
  single-source dramatic lighting, documentary-realist (not cartoonish, not photographic)
- anatomy: hands, faces, limbs, proportions — count fingers, check eyes
- artifacts: warping, smearing, duplicated limbs, melted objects
- text_artifacts: ANY garbled or invented lettering visible in the frame is a serious
  defect; score 0-30 if readable-looking text appears
- composition: subject scale, framing, headroom, balance
- historical_accuracy: period-correct clothing, props, vehicles, architecture

Set `usable` false if the frame cannot go on screen in a serious documentary.
When usable is false, write `repair_instruction`: a concrete change to the prompt that
would fix it (e.g. "remove the signage from the background", "show the officer from
behind to avoid facial distortion", "widen to a full shot so hands are not in frame").

Return ONLY JSON:
{"results": [{"scene_id": "S0001", "scores": {"prompt_adherence": 0, "style_consistency": 0,
 "anatomy": 0, "artifacts": 0, "text_artifacts": 0, "composition": 0,
 "historical_accuracy": 0}, "overall": 0, "usable": true, "problems": ["..."],
 "repair_instruction": ""}]}"""


def png_dimensions(path: Path) -> tuple[int, int] | None:
    """Read PNG/JPEG dimensions without a heavy imaging dependency."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        w, h = struct.unpack(">II", data[16:24])
        return int(w), int(h)
    if data[:2] == b"\xff\xd8":  # JPEG: walk the segment markers
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return int(w), int(h)
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            seg = struct.unpack(">H", data[i + 2:i + 4])[0]
            i += 2 + seg
    return None


class ImageQAAgent(BaseAgent):
    agent_id = "image-qa-agent"
    name = "Image Quality Agent"
    version = 1
    capabilities = ["evaluate_images", "detect_visual_defects"]
    task_kind = "image_qa"
    stage = "IMAGE_QA"
    quality_threshold = 88.0

    async def execute(self, ctx: AgentContext, *, storyboard: Storyboard) -> dict:
        threshold = float(ctx.settings.gate_visual)
        scenes = [s for s in storyboard.scenes if s.image_path]
        results: dict[str, dict] = {}

        for bi in range(0, len(scenes), BATCH):
            batch = scenes[bi:bi + BATCH]
            await ctx.progress("IMAGE_QA", 100 * bi / max(len(scenes), 1),
                               f"inspecting {bi}/{len(scenes)}")
            valid: list[Scene] = []
            for s in batch:
                p = Path(s.image_path)
                dims = png_dimensions(p)
                if not p.exists() or p.stat().st_size < 2048:
                    results[s.scene_id] = {"overall": 0, "usable": False,
                                           "problems": ["file missing or truncated"],
                                           "repair_instruction": ""}
                    continue
                if dims:
                    ratio = dims[0] / dims[1]
                    if not (1.5 <= ratio <= 2.0):  # 16:9 is 1.78
                        results[s.scene_id] = {
                            "overall": 0, "usable": False,
                            "problems": [f"wrong aspect ratio {dims[0]}x{dims[1]}"],
                            "repair_instruction": "regenerate at 16:9"}
                        continue
                valid.append(s)

            if not valid:
                continue
            listing = "\n".join(
                f"Image {i + 1} = {s.scene_id}. Prompt was: {s.image_prompt[:600]}"
                for i, s in enumerate(valid))
            data = await self.vision_json(
                ctx, system=SYSTEM,
                user=f"Inspect these {len(valid)} frames in order.\n\n{listing}",
                images=[Path(s.image_path) for s in valid], max_tokens=8000)
            for r in data.get("results", []):
                results[r.get("scene_id", "")] = r

        failed: list[dict] = []
        total = 0.0
        for s in scenes:
            r = results.get(s.scene_id, {"overall": 0, "usable": False,
                                         "problems": ["not evaluated"],
                                         "repair_instruction": ""})
            s.qa_score = float(r.get("overall", 0))
            total += s.qa_score
            if not r.get("usable", False) or s.qa_score < threshold:
                failed.append({"scene_id": s.scene_id, "score": s.qa_score,
                               "problems": r.get("problems", []),
                               "repair_instruction": r.get("repair_instruction", "")})

        average = total / len(scenes) if scenes else 0.0
        report = {"average_score": round(average, 1), "evaluated": len(scenes),
                  "failed": failed, "threshold": threshold}
        ctx.store.save_json("qa/image_qa.json", report)
        ctx.store.save_json("storyboard/storyboard.json", storyboard)
        await ctx.emit("IMAGE_QA_DONE", {"average": report["average_score"],
                                         "failed": len(failed)})
        return report


class VisualRepairAgent(BaseAgent):
    """Repairs a failing scene by rewriting its prompt, not by retrying it unchanged."""
    agent_id = "visual-repair-agent"
    name = "Visual Repair Agent"
    version = 1
    capabilities = ["repair_image_prompt"]
    task_kind = "visual_direction"
    stage = "VISUAL_REPAIR"

    REPAIR_SYSTEM = """You repair image prompts that produced defective frames.

Apply the repair instruction and the reported problems. Prefer structural fixes that
remove the failure mode entirely rather than adding negative words:
- anatomy/hand defects -> change framing so hands are out of frame, or show the subject
  from behind or in silhouette
- garbled text -> remove every element that would carry lettering (signs, newspapers,
  screens, banners) and describe the prop as blank or turned away
- style drift -> restate the style requirements explicitly and simplify the scene
- artifacts -> reduce the number of subjects and simplify the composition

Keep the same narrative meaning, the same characters, and the exact style suffix.
Return ONLY JSON: {"prompt": "the single-paragraph repaired prompt"}"""

    async def execute(self, ctx: AgentContext, *, scene: Scene, problems: list[str],
                      repair_instruction: str, attempt: int = 1) -> str:
        style_suffix = ctx.visual_dna.get("style_suffix", "")
        simplify = ("\nThis prompt has failed repeatedly. Aggressively simplify: a single "
                    "subject or an empty environment, wide framing, minimal props."
                    if attempt >= 2 else "")
        user = (
            f"FAILED PROMPT:\n{scene.image_prompt}\n\n"
            f"REPORTED PROBLEMS: {'; '.join(problems) or 'low quality score'}\n"
            f"REPAIR INSTRUCTION: {repair_instruction or 'fix the reported problems'}\n"
            f"NARRATION THIS FRAME ILLUSTRATES: {scene.narration_text[:300]}\n"
            f"STYLE SUFFIX (keep verbatim): {style_suffix}{simplify}"
        )
        data = await self.llm_json(ctx, system=self.REPAIR_SYSTEM, user=user, max_tokens=3000,
                                   effort="medium")
        prompt = " ".join(str(data.get("prompt", scene.image_prompt)).split())
        if style_suffix and style_suffix.lower() not in prompt.lower():
            prompt = f"{prompt.rstrip('. ')}. {style_suffix}"
        scene.image_prompt = prompt
        return prompt


register(ImageQAAgent())
register(VisualRepairAgent())
