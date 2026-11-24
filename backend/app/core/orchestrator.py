"""Production orchestrator — the resumable DAG executor.

Every stage is a checkpoint. Completed stages are recorded on the project row and their
outputs live in the artifact store, so a crash at MOTION resumes at MOTION rather than
re-researching the topic. Stages that have no dependency on each other are run
concurrently.

The Chief Producer (app/agents/chief_producer.py) decides; this module executes.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from app.agents import registry as agent_registry
from app.agents.base import AgentContext
from app.agents.notification import NotificationAgent
from app.core import events
from app.core.artifacts import ArtifactStore
from app.core.costs import project_costs
from app.core.dna import load_channel_dna
from app.core.errors import (
    PolicyBlockedError,
    ProjectBlockedError,
    ProviderUnavailableError,
    QualityGateFailure,
)
from app.db.base import get_session_factory
from app.db.models import Project
from app.schemas.core import (
    CharacterProfile,
    ProductionPlan,
    QAReport,
    RequirementSpec,
    ResearchDossier,
    Script,
    Storyboard,
    StoryStructure,
    VoiceDirective,
    YouTubeMetadata,
)

log = logging.getLogger("agency.orchestrator")

STAGES = [
    "REQUIREMENTS", "PLANNING", "RESEARCH", "FACT_CHECK", "STORY", "SCRIPT",
    "STORYBOARD", "CHARACTERS", "VISUAL_DIRECTION", "PRODUCTION_FANOUT",
    "IMAGE_QA", "MOTION", "VIDEO_QA", "MUSIC", "SUBTITLES", "EDIT", "AUDIO_QA",
    "PACKAGING", "COPYRIGHT", "POLICY", "FINAL_QA", "PUBLISH", "NOTIFY",
]


class ProductionRun:
    """One project's execution. Holds the working set in memory and on disk."""

    def __init__(self, project_id: str, channel_id: str, request_text: str):
        self.project_id = project_id
        self.channel_id = channel_id
        self.request_text = request_text
        self.store = ArtifactStore(project_id)
        self.ctx = AgentContext(project_id=project_id, channel_id=channel_id,
                                dna=load_channel_dna(channel_id), store=self.store)
        self.started = time.monotonic()
        self.cancelled = asyncio.Event()

        # Working set — repopulated from artifacts when resuming.
        self.requirement: RequirementSpec | None = None
        self.plan: ProductionPlan | None = None
        self.dossier: ResearchDossier | None = None
        self.structure: StoryStructure | None = None
        self.script: Script | None = None
        self.storyboard: Storyboard | None = None
        self.characters: list[CharacterProfile] = []
        self.directive: VoiceDirective | None = None
        self.music: Path | None = None
        self.subtitle_burn: Path | None = None
        self.final_video: Path | None = None
        self.metadata: YouTubeMetadata | None = None
        self.thumbnail_title = ""
        self.policy_report: dict = {}
        self.copyright_report: dict = {}
        self.qa: QAReport | None = None

    # ── persistence ────────────────────────────────────────────────
    async def _update(self, **fields) -> None:
        async with get_session_factory()() as session:
            project = await session.get(Project, self.project_id)
            if not project:
                return
            for key, value in fields.items():
                setattr(project, key, value)
            await session.commit()

    async def _completed(self) -> list[str]:
        async with get_session_factory()() as session:
            project = await session.get(Project, self.project_id)
            return list(project.completed_stages or []) if project else []

    async def _mark_done(self, stage: str) -> None:
        async with get_session_factory()() as session:
            project = await session.get(Project, self.project_id)
            if project:
                done = list(project.completed_stages or [])
                if stage not in done:
                    done.append(stage)
                project.completed_stages = done
                project.current_stage = stage
                await session.commit()

    def _restore(self) -> None:
        """Rebuild the working set from artifacts so any stage can resume cold."""
        s = self.store
        self.requirement = s.load_model("requirements.json", RequirementSpec)
        self.plan = s.load_model("plan.json", ProductionPlan)
        self.dossier = (s.load_model("research/facts.json", ResearchDossier)
                        or s.load_model("research/dossier.json", ResearchDossier))
        self.structure = s.load_model("script/story_structure.json", StoryStructure)
        final_script = s.load_json("script/final_script.json")
        if final_script:
            self.script = Script.model_validate(final_script)
        self.storyboard = s.load_model("storyboard/storyboard.json", Storyboard)
        chars = s.load_json("storyboard/characters.json")
        if chars:
            self.characters = [CharacterProfile.model_validate(c) for c in chars]
        self.directive = s.load_model("audio/voice_directive.json", VoiceDirective)
        self.metadata = s.load_model("metadata/youtube.json", YouTubeMetadata)
        self.policy_report = s.load_json("qa/policy.json") or {}
        self.copyright_report = s.load_json("qa/copyright.json") or {}
        if s.exists("final/video.mp4"):
            self.final_video = s.path("final/video.mp4")
        for rel in ("subtitles/hi.srt", "subtitles/en.srt"):
            if s.exists(rel):
                self.subtitle_burn = s.path(rel)
                break

    # ── driver ─────────────────────────────────────────────────────
    async def run(self) -> None:
        completed = await self._completed()
        if completed:
            log.info("resuming project %s after %s", self.project_id, completed[-1])
            self._restore()
            await events.emit(self.project_id, "PROJECT_RESUMED",
                              {"from_stage": self._next_stage(completed)})
        await self._update(status="running", error="", blocked_on="")
        await events.emit(self.project_id, "PROJECT_STARTED", {"channel": self.channel_id})

        try:
            for stage in STAGES:
                if self.cancelled.is_set():
                    await self._update(status="cancelled")
                    await events.emit(self.project_id, "PROJECT_CANCELLED", {"stage": stage})
                    return
                if stage in completed:
                    continue
                await self._update(current_stage=stage)
                await events.emit(self.project_id, "STAGE_STARTED", {"stage": stage})
                await getattr(self, f"_stage_{stage.lower()}")()
                await self._mark_done(stage)
                await events.emit(self.project_id, "STAGE_COMPLETED", {"stage": stage})

            costs = await project_costs(self.project_id)
            await self._update(status="done", current_stage="DONE",
                               total_cost_usd=costs["total_usd"])
            await events.emit(self.project_id, "PROJECT_DONE", {
                "duration_seconds": round(time.monotonic() - self.started, 1),
                "cost_usd": costs["total_usd"]})

        except ProjectBlockedError as e:
            await self._block(e.dependency, str(e))
        except ProviderUnavailableError as e:
            missing = ", ".join(e.missing) if e.missing else e.provider
            await self._block(missing, str(e))
        except PolicyBlockedError as e:
            await self._block("policy_review", str(e))
        except QualityGateFailure as e:
            await self._block(f"quality_gate:{e.stage}",
                              f"{e} — {e.report.get('reason', '')}")
        except asyncio.CancelledError:
            await self._update(status="cancelled")
            raise
        except Exception as e:
            log.exception("project %s failed", self.project_id)
            await self._update(status="failed", error=f"{type(e).__name__}: {e}"[:2000])
            await events.emit(self.project_id, "PROJECT_FAILED", {"error": str(e)[:500]})
            await self._notify_failure(str(e))

    def _next_stage(self, completed: list[str]) -> str:
        for stage in STAGES:
            if stage not in completed:
                return stage
        return "DONE"

    async def _block(self, dependency: str, detail: str) -> None:
        log.warning("project %s BLOCKED on %s: %s", self.project_id, dependency, detail)
        await self._update(status="blocked", blocked_on=dependency, error=detail[:2000])
        await events.emit(self.project_id, "PROJECT_BLOCKED",
                          {"dependency": dependency, "detail": detail[:500]})
        await self._notify_failure(f"Blocked on: {dependency}\n\n{detail}")

    async def _notify_failure(self, detail: str) -> None:
        try:
            agent: NotificationAgent = agent_registry.get("notification-agent")
            await agent.run(self.ctx,
                            title=f"Production stopped: {self.requirement.topic if self.requirement else self.request_text[:60]}",
                            body=detail[:1500])
        except Exception:
            log.debug("failure notification could not be delivered", exc_info=True)

    # ── stages ─────────────────────────────────────────────────────

    async def _stage_requirements(self) -> None:
        agent = agent_registry.get("requirements-agent")
        self.requirement = await agent.run(self.ctx, request_text=self.request_text)
        if self.requirement.topic == "TOPIC_DISCOVERY_REQUIRED":
            discovery = agent_registry.get("topic-discovery-agent")
            topics = await discovery.run(self.ctx, count=8)
            if not topics:
                raise ProjectBlockedError(
                    "topic_selection",
                    "no sufficiently documented topic could be found automatically")
            self.requirement.topic = topics[0]["title"]
            self.store.save_json("requirements.json", self.requirement)
        await self._update(requirement=self.requirement.model_dump())

    async def _stage_planning(self) -> None:
        agent = agent_registry.get("planner-agent")
        self.plan = await agent.run(self.ctx, requirement=self.requirement)
        await self._update(plan=self.plan.model_dump())

        # Capability-gap handling: degrade where safe, block where not.
        from app.providers import registry as provider_registry
        report = provider_registry.capability_report()
        if not report["image"]["ready"]:
            raise ProjectBlockedError(
                "image_provider",
                "No image generation provider is configured. Set one of: "
                + ", ".join(report["image"]["missing_credentials"]))
        if not report["tts"]["ready"]:
            raise ProjectBlockedError(
                "tts_provider",
                "No text-to-speech provider is available. Set ELEVENLABS_API_KEY or "
                "install edge-tts.")
        if not report["video"]["ready"]:
            raise ProjectBlockedError("ffmpeg", "FFmpeg is required to render video.")
        if self.plan.youtube_upload and not report["youtube"]["ready"]:
            # Not fatal: render and hold rather than losing the whole production.
            log.warning("YouTube not connected — will render and stop before publishing")

    async def _stage_research(self) -> None:
        agent = agent_registry.get("research-agent")
        self.dossier = await agent.run(self.ctx, topic=self.requirement.topic,
                                       depth=self.plan.research_depth)

    async def _stage_fact_check(self) -> None:
        agent = agent_registry.get("fact-check-agent")
        self.dossier = await agent.run(self.ctx, dossier=self.dossier)

    async def _stage_story(self) -> None:
        agent = agent_registry.get("story-architect-agent")
        self.structure = await agent.run(self.ctx, dossier=self.dossier,
                                         word_target=self.plan.word_count_target)

    async def _stage_script(self) -> None:
        """Write → critique → revise, until the gate passes or the loop budget is spent."""
        writer = agent_registry.get("script-agent")
        critic = agent_registry.get("script-critic-agent")
        gate = float(self.ctx.settings.gate_script)
        max_loops = int(self.ctx.settings.max_revision_loops)

        script = await writer.run(self.ctx, dossier=self.dossier, structure=self.structure,
                                  word_target=self.plan.word_count_target,
                                  language=self.requirement.language)
        best, best_report = script, None

        for loop in range(max_loops):
            report = await critic.run(self.ctx, script=script, dossier=self.dossier,
                                      word_target=self.plan.word_count_target)
            if best_report is None or report.overall > best_report.overall:
                best, best_report = script, report

            if report.verdict == "approve" and report.overall >= gate:
                break
            if report.verdict == "reject" and loop == max_loops - 1:
                raise QualityGateFailure("SCRIPT", report.overall, gate,
                                         {"reason": "critic rejected the script",
                                          "problems": report.problems[:5]})
            if loop == max_loops - 1:
                break
            await events.emit(self.project_id, "SCRIPT_REVISION",
                              {"loop": loop + 1, "score": report.overall})
            script = await writer.run(self.ctx, dossier=self.dossier, structure=self.structure,
                                      word_target=self.plan.word_count_target,
                                      language=self.requirement.language,
                                      previous=script, critique=report.model_dump())

        if best_report and best_report.overall < gate:
            # Factual failure is fatal; craft shortfall is not.
            if best_report.scores.get("factual_accuracy", 0) < 90:
                raise QualityGateFailure("SCRIPT", best_report.overall, gate, {
                    "reason": "script could not be brought to factual accuracy standard",
                    "unsupported_claims": best_report.unsupported_claims[:8]})
            log.warning("script below craft gate (%.1f < %.1f) but factually sound — proceeding",
                        best_report.overall, gate)

        self.script = best
        self.store.save_text("script/final_script.txt", best.text)
        self.store.save_json("script/final_script.json", best)
        await events.emit(self.project_id, "SCRIPT_APPROVED", {
            "version": best.version, "words": best.word_count,
            "score": best_report.overall if best_report else 0,
            "duration_estimates": best.duration_estimates})

    async def _stage_storyboard(self) -> None:
        from app.agents.planner import NARRATION_WPM
        wpm = NARRATION_WPM.get(self.requirement.language.split("-")[0].lower(),
                                NARRATION_WPM["default"])
        agent = agent_registry.get("storyboard-agent")
        self.storyboard = await agent.run(self.ctx, script=self.script, wpm=wpm)

    async def _stage_characters(self) -> None:
        agent = agent_registry.get("character-agent")
        self.characters = await agent.run(self.ctx, script=self.script, dossier=self.dossier)

    async def _stage_visual_direction(self) -> None:
        agent = agent_registry.get("visual-director-agent")
        self.storyboard = await agent.run(self.ctx, storyboard=self.storyboard,
                                          characters=self.characters,
                                          topic=self.requirement.topic)

    async def _stage_production_fanout(self) -> None:
        """Narration and imagery have no dependency on each other — run both at once."""
        async def audio_branch() -> None:
            director = agent_registry.get("voice-director-agent")
            self.directive = await director.run(self.ctx, script_text=self.script.text,
                                                language=self.requirement.language)
            voice = agent_registry.get("voice-agent")
            self.storyboard = await voice.run(self.ctx, storyboard=self.storyboard,
                                              directive=self.directive)

        async def image_branch() -> None:
            images = agent_registry.get("image-agent")
            await images.run(self.ctx, storyboard=self.storyboard)

        # Both branches mutate distinct fields of the same scene objects.
        await asyncio.gather(audio_branch(), image_branch())
        self.store.save_json("storyboard/storyboard.json", self.storyboard)

    async def _stage_image_qa(self) -> None:
        """Detect → diagnose → repair prompt → regenerate → re-validate."""
        qa = agent_registry.get("image-qa-agent")
        repair = agent_registry.get("visual-repair-agent")
        images = agent_registry.get("image-agent")
        by_id = {s.scene_id: s for s in self.storyboard.scenes}

        for attempt in range(1, int(self.ctx.settings.max_revision_loops) + 1):
            report = await qa.run(self.ctx, storyboard=self.storyboard)
            failed = report["failed"]
            if not failed:
                break
            ratio = len(failed) / max(len(self.storyboard.scenes), 1)
            log.info("image QA pass %d: %d failures (%.0f%%)", attempt, len(failed), ratio * 100)
            if attempt == self.ctx.settings.max_revision_loops:
                break

            await events.emit(self.project_id, "REPAIR_REQUIRED",
                              {"stage": "IMAGE_QA", "count": len(failed), "attempt": attempt})
            targets = []
            for item in failed:
                scene = by_id.get(item["scene_id"])
                if not scene:
                    continue
                await repair.run(self.ctx, scene=scene, problems=item.get("problems", []),
                                 repair_instruction=item.get("repair_instruction", ""),
                                 attempt=attempt)
                scene.image_path = ""  # force regeneration
                targets.append(scene.scene_id)
            if targets:
                await images.run(self.ctx, storyboard=self.storyboard, only_scenes=targets)

        # A scene that never produced a usable image is dropped rather than shipped.
        unusable = [s for s in self.storyboard.scenes if not s.image_path]
        if unusable:
            log.warning("%d scenes have no image and will be excluded", len(unusable))
        if not any(s.image_path for s in self.storyboard.scenes):
            raise QualityGateFailure("IMAGE_QA", 0, float(self.ctx.settings.gate_visual),
                                     {"reason": "no usable imagery was produced"})
        self.store.save_json("storyboard/storyboard.json", self.storyboard)

    async def _stage_motion(self) -> None:
        agent = agent_registry.get("motion-agent")
        self.storyboard = await agent.run(self.ctx, storyboard=self.storyboard)

    async def _stage_video_qa(self) -> None:
        qa = agent_registry.get("video-qa-agent")
        motion = agent_registry.get("motion-agent")
        report = await qa.run(self.ctx, storyboard=self.storyboard)
        failed = [f["scene_id"] for f in report["failed"]]
        if failed:
            await events.emit(self.project_id, "REPAIR_REQUIRED",
                              {"stage": "VIDEO_QA", "count": len(failed)})
            by_id = {s.scene_id: s for s in self.storyboard.scenes}
            for sid in failed:
                if sid in by_id:
                    by_id[sid].clip_path = ""
            await motion.run(self.ctx, storyboard=self.storyboard, only_scenes=failed)
            await qa.run(self.ctx, storyboard=self.storyboard)

    async def _stage_music(self) -> None:
        agent = agent_registry.get("music-agent")
        self.music = await agent.run(self.ctx,
                                     duration_seconds=self.storyboard.total_duration,
                                     mood="grave")

    async def _stage_subtitles(self) -> None:
        agent = agent_registry.get("subtitle-agent")
        paths = await agent.run(self.ctx, storyboard=self.storyboard,
                                language=self.requirement.language, translate_to="en")
        lang = self.requirement.language.split("-")[0]
        # Burn in the source-language track; upload both as caption tracks.
        key = f"{lang}_srt"
        if key in paths:
            self.subtitle_burn = Path(paths[key])

    async def _stage_edit(self) -> None:
        agent = agent_registry.get("editor-agent")
        self.final_video = await agent.run(
            self.ctx, storyboard=self.storyboard, music_path=self.music,
            subtitle_path=self.subtitle_burn,
            watermark=self.ctx.channel_dna.get("channel_name", ""))

    async def _stage_audio_qa(self) -> None:
        agent = agent_registry.get("audio-qa-agent")
        mixed = self.store.path("final/audio_master.wav")
        report = await agent.run(self.ctx, storyboard=self.storyboard,
                                 mixed_audio=mixed if mixed.exists() else None)
        if report["score"] < float(self.ctx.settings.gate_voice):
            bad = [f["scene_id"] for f in report["failed"] if f["scene_id"] != "MIX"]
            if bad:
                await events.emit(self.project_id, "REPAIR_REQUIRED",
                                  {"stage": "AUDIO_QA", "count": len(bad)})
                voice = agent_registry.get("voice-agent")
                by_id = {s.scene_id: s for s in self.storyboard.scenes}
                for sid in bad:
                    if sid in by_id:
                        by_id[sid].audio_path = ""
                await voice.run(self.ctx, storyboard=self.storyboard,
                                directive=self.directive, only_scenes=bad)
                # Durations changed — the film must be re-cut and re-rendered.
                await self._recut_after_audio_repair()

    async def _recut_after_audio_repair(self) -> None:
        motion = agent_registry.get("motion-agent")
        for scene in self.storyboard.scenes:
            scene.clip_path = ""
        await motion.run(self.ctx, storyboard=self.storyboard)
        await self._stage_subtitles()
        await self._stage_edit()

    async def _stage_packaging(self) -> None:
        """Thumbnail and metadata are independent — produce them together."""
        summary = (self.dossier.summary if self.dossier else "") + "\n\n" + self.script.text[:6000]

        async def thumbs() -> None:
            agent = agent_registry.get("thumbnail-agent")
            concepts = await agent.run(self.ctx, topic=self.requirement.topic,
                                       script_summary=summary,
                                       variants=self.plan.thumbnail_variants)
            if concepts:
                self.thumbnail_title = concepts[0].hindi_title

        await thumbs()  # SEO uses the winning headline, so thumbnails go first
        seo = agent_registry.get("seo-agent")
        self.metadata = await seo.run(self.ctx, topic=self.requirement.topic,
                                      script_summary=summary, storyboard=self.storyboard,
                                      language=self.requirement.language,
                                      thumbnail_title=self.thumbnail_title)

    async def _stage_copyright(self) -> None:
        agent = agent_registry.get("copyright-agent")
        self.copyright_report = await agent.run(self.ctx, storyboard=self.storyboard,
                                                music_path=self.music,
                                                final_video=self.final_video)
        if self.copyright_report["status"] == "BLOCK":
            raise PolicyBlockedError(
                [i["problem"] for i in self.copyright_report["issues"]
                 if i["severity"] == "critical"])

    async def _stage_policy(self) -> None:
        agent = agent_registry.get("policy-agent")
        thumbnail = self.store.path("thumbnail/thumbnail_final.png")
        self.policy_report = await agent.run(
            self.ctx, script_text=self.script.text, metadata=self.metadata,
            thumbnail_path=thumbnail if thumbnail.exists() else None)
        if self.policy_report["verdict"] == "BLOCK":
            raise PolicyBlockedError([f["description"] for f in self.policy_report["findings"]
                                      if f.get("severity") == "critical"])
        if self.policy_report["verdict"] == "FIX":
            raise PolicyBlockedError(
                [f"unresolved policy finding: {f['description']}"
                 for f in self.policy_report["findings"]
                 if f.get("severity") in {"critical", "high"}])

    async def _stage_final_qa(self) -> None:
        agent = agent_registry.get("final-qa-agent")
        gate = float(self.ctx.settings.gate_final)
        summary = self.dossier.summary if self.dossier else self.requirement.topic

        for attempt in range(1, int(self.ctx.settings.max_revision_loops) + 1):
            self.qa = await agent.run(
                self.ctx, final_video=self.final_video, storyboard=self.storyboard,
                metadata=self.metadata, policy_report=self.policy_report,
                copyright_report=self.copyright_report, script_summary=summary)
            await self._update(quality=self.qa.model_dump())

            if self.qa.overall_score >= gate and self.qa.policy == "PASS":
                await events.emit(self.project_id, "QA_APPROVED",
                                  {"score": self.qa.overall_score})
                return
            if attempt == self.ctx.settings.max_revision_loops:
                break

            await events.emit(self.project_id, "QA_FAILED", {
                "score": self.qa.overall_score, "attempt": attempt,
                "issues": len(self.qa.issues)})
            repaired = await self._dispatch_repairs(self.qa.issues)
            if not repaired:
                break  # nothing actionable — another QA pass would find the same thing

        if self.qa.policy != "PASS":
            raise PolicyBlockedError(["final QA policy check did not pass"])
        raise QualityGateFailure("FINAL_QA", self.qa.overall_score, gate,
                                 {"reason": "quality could not be brought to the publish bar",
                                  "issues": self.qa.issues[:8]})

    async def _dispatch_repairs(self, issues: list[dict]) -> bool:
        """Route each defect to the responsible specialist, then re-render."""
        actionable = [i for i in issues if i.get("severity") in {"critical", "high"}]
        if not actionable:
            return False
        agents_needed = {i.get("repair_agent", "") for i in actionable}
        log.info("dispatching repairs to: %s", ", ".join(sorted(a for a in agents_needed if a)))
        await events.emit(self.project_id, "REPAIR_REQUIRED",
                          {"stage": "FINAL_QA", "agents": sorted(agents_needed)})

        did_something = False
        if "seo-agent" in agents_needed or "thumbnail-agent" in agents_needed:
            await self._stage_packaging()
            did_something = True
        if "voice-agent" in agents_needed:
            await self._stage_audio_qa()
            did_something = True
        if agents_needed & {"editor-agent", "image-agent", "motion-agent"}:
            if "image-agent" in agents_needed:
                await self._stage_image_qa()
                for scene in self.storyboard.scenes:
                    scene.clip_path = ""
                await self._stage_motion()
            await self._stage_edit()
            did_something = True
        return did_something

    async def _stage_publish(self) -> None:
        if self.requirement.publication_goal != "publish":
            log.info("publication_goal=%s — skipping upload", self.requirement.publication_goal)
            await events.emit(self.project_id, "PUBLISH_SKIPPED",
                              {"reason": self.requirement.publication_goal})
            return
        from app.providers import registry as provider_registry
        if not provider_registry.capability_report()["youtube"]["ready"]:
            raise ProjectBlockedError(
                "youtube_oauth",
                "The video is rendered and ready, but no YouTube account is connected. "
                "Connect one via POST /api/youtube/connect, then retry this project.")

        agent = agent_registry.get("publisher-agent")
        result = await agent.run(self.ctx, final_video=self.final_video,
                                 metadata=self.metadata,
                                 privacy_status=self.requirement.privacy_status)
        await self._update(publish_result=result.model_dump())

    async def _stage_notify(self) -> None:
        agent: NotificationAgent = agent_registry.get("notification-agent")
        costs = await project_costs(self.project_id)
        publish = self.store.load_json("metadata/publish_result.json") or {}
        body = NotificationAgent.success_body(
            video_title=self.metadata.title if self.metadata else self.requirement.topic,
            duration_seconds=self.storyboard.total_duration if self.storyboard else 0,
            quality=self.qa.overall_score if self.qa else 0,
            production_seconds=time.monotonic() - self.started,
            cost_usd=costs["total_usd"],
            privacy=publish.get("privacy_status", self.requirement.privacy_status))
        title = ("Your video has been published" if publish.get("video_url")
                 else "Your video is rendered and ready")
        await agent.run(self.ctx, title=title, body=body,
                        url=publish.get("video_url", ""))
