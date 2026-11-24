# Architecture

## The core idea

This is not a prompt chain. It is a **resumable production graph** with typed contracts
between stages, measurable quality gates, and an autonomous repair loop.

Three properties do most of the work:

1. **Every stage output is a typed artifact on disk.** The working set can be rebuilt from
   the artifact store at any point, so a crash at scene 94 of 150 resumes at scene 94.
2. **Narration drives the timeline.** Each scene's audio is synthesized separately and
   its *measured* duration becomes the length of that scene's video clip. Picture and
   sound cannot drift apart, because they are cut from the same measurement.
3. **A provider that cannot run raises instead of pretending.** `ProviderUnavailableError`
   propagates through a fallback chain; if the whole chain is dead the project is BLOCKED
   with the exact environment variable that would unblock it.

## Stage graph

```
REQUIREMENTS → PLANNING → RESEARCH → FACT_CHECK → STORY → SCRIPT ⟲ critic
                                                              ↓
                            STORYBOARD → CHARACTERS → VISUAL_DIRECTION
                                                              ↓
                                    ┌─── PRODUCTION_FANOUT ───┐
                                    │  images ∥ voice-direct  │
                                    │           + voice       │
                                    └────────────┬────────────┘
                                    IMAGE_QA ⟲ repair → MOTION → VIDEO_QA ⟲
                                                              ↓
                              MUSIC → SUBTITLES → EDIT → AUDIO_QA ⟲ recut
                                                              ↓
                          PACKAGING (thumbnail → SEO) → COPYRIGHT → POLICY
                                                              ↓
                                          FINAL_QA ⟲ dispatch repairs
                                                              ↓
                                                   PUBLISH → NOTIFY
```

`⟲` marks a closed loop: detect → diagnose → assign specialist → repair → re-validate.

### Why this ordering

- **Voice before motion.** Clip durations come from measured narration audio. Generating
  motion first would mean stretching or trimming video to fit audio later — the classic
  source of drift.
- **Thumbnail before SEO.** The SEO agent receives the winning thumbnail headline so the
  title complements it rather than duplicating it.
- **Subtitles before edit.** Burn-in happens during the final mux, in one pass.
- **Copyright and policy before final QA.** Final QA reports `policy: PASS|FAIL`, and it
  needs both upstream verdicts to do that.

## Agent roster

| Layer | Agents |
|---|---|
| Orchestration | chief-producer, planner-agent, agent-factory |
| Research | research-agent, fact-check-agent, topic-discovery-agent |
| Story | story-architect-agent, script-agent, script-critic-agent |
| Design | storyboard-agent, character-agent, visual-director-agent |
| Visual | image-agent, image-qa-agent, visual-repair-agent, motion-agent, video-qa-agent |
| Audio | voice-director-agent, voice-agent, audio-qa-agent, music-agent |
| Assembly | editor-agent, subtitle-agent |
| Packaging | thumbnail-agent, seo-agent |
| Gates | copyright-agent, policy-agent, final-qa-agent |
| Release | publisher-agent, notification-agent |

31 agents total. Each declares `capabilities`, `task_kind` (which selects a model tier),
`quality_threshold`, and `max_attempts`.

## Quality gates

Gates are configurable via environment variables (`GATE_*`). Two are treated differently
from the rest:

- **Factual accuracy is fatal.** If the script cannot reach factual standard after the
  revision budget, the project blocks. A craft shortfall (pacing, rhythm) with sound facts
  proceeds with a logged warning — the video is publishable, just not the channel's best.
- **A critical QA issue cannot be averaged away.** `overall_score` is capped at 50 when any
  issue has severity `critical`, so a broken render can never average its way past the gate.

## The repair loop

Final QA emits issues that each name a `repair_agent`. The orchestrator dispatches:

| Issue names | Action |
|---|---|
| `seo-agent`, `thumbnail-agent` | re-run PACKAGING |
| `voice-agent` | regenerate failing segments, then re-cut and re-render |
| `image-agent` | repair prompts, regenerate, re-animate, re-render |
| `editor-agent`, `motion-agent` | re-render |

If nothing is actionable the loop exits rather than burning the budget re-detecting the
same defect.

Image repair is prompt-level, not retry-level: `visual-repair-agent` rewrites the prompt
to remove the *failure mode* (frame hands out of shot, delete text-carrying props,
simplify the composition) rather than resubmitting identical input and hoping.

## Prompt-injection defense

Research retrieves untrusted web content. Every agent that handles external text wraps it
with `wrap_untrusted()`, which fences the data and states that instructions inside it carry
no authority. The trust boundaries are explicit: system prompt → agent instructions →
user requirement → external data → tool output, in descending authority.

Agents receive no ambient capability. They get an `AgentContext` with a scoped artifact
store, the channel DNA, and an event emitter. No shell, no arbitrary filesystem, no
credentials. The artifact store rejects path traversal.

## Agent Factory

When the planner finds a capability no agent provides, the factory builds one — as a
**declarative `AgentSpec`** (system prompt, IO schemas, model tier, test cases), never as
generated Python. A `DynamicAgent` interprets the spec at runtime.

This is a deliberate trade. Code generation into a live process is a remote-code-execution
hole that no amount of review prompting closes. A declarative agent delivers a real new
capability with a blast radius of one prompt.

Promotion is enforced in code:

```
sandbox → test (generated cases must pass ≥80%) → staging (security review)
        → production (registered, dispatchable)
```

A spec that requires an external tool or credential is refused outright — the factory
does not invent integrations. It reports the gap instead.

## Model routing

`app/core/router.py` maps each task kind to a tier, and escalates one tier on attempt 3+:

| Tier | Default | Used for |
|---|---|---|
| heavy | `claude-opus-5` | research, fact-check, script, critics, policy, final QA |
| standard | `claude-sonnet-5` | storyboard, visual direction, SEO, thumbnails, image QA |
| light | `claude-haiku-4-5` | classification, formatting, subtitle translation |

## Cost model

Every LLM call, image, and TTS request writes a `CostRecord` keyed by project and agent,
rolled up onto the project. The planner estimates before execution; `/api/projects/{id}`
reports actuals broken down by kind.

## Data model

`Project`, `TaskRecord`, `AgentExecution`, `EventRecord`, `CostRecord`,
`AgentRegistryRecord`, `MemoryRecord`, `ChannelRecord`. Stage payloads are stored as JSON
so schema evolution does not require a migration for every contract change.

## Channel DNA

Four JSON files per channel (`channel`, `script`, `visual`, `thumbnail`) under
`channel_dna/<id>/`. They are loaded at startup, mirrored into the `channels` table, and
injected into every agent through `AgentContext`. Adding a second channel is adding a
directory — no code changes.

### One deviation from the supplied AniDoc spec

The source document states a 3,000–4,500 word target *and* "approx. 10–15 min video at
100 wpm". Those are inconsistent: 3,000 words at 100 wpm is 30 minutes. Real Hindi
documentary narration runs ≈130 wpm, making that band a 23–35 minute video.

The planner therefore treats the **requested duration as authoritative** and derives word
count from it (`words_for_duration`), recording any conflict with the DNA band in
`plan.notes`. A request for 12 minutes produces a 12-minute video. To restore the literal
band behaviour, edit `word_count_target` in `script_dna.json` and adjust `NARRATION_WPM`.
