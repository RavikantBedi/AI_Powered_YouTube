"""Typed contracts passed between agents. Every stage's output is one of these models,
persisted to the artifact store as JSON, so any stage can be resumed or replayed."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

# ── Requirements & planning ─────────────────────────────────────────────

class RequirementSpec(BaseModel):
    topic: str
    language: str = "hi-IN"
    duration_seconds: int = 720
    genre: str = "documentary"
    audience: str = ""
    storytelling_style: str = ""
    visual_style: str = ""
    narration_style: str = ""
    platform: str = "youtube"
    aspect_ratio: str = "16:9"
    publication_goal: Literal["publish", "draft", "render_only"] = "publish"
    quality_level: Literal["standard", "high", "max"] = "high"
    factuality_requirement: Literal["strict", "standard"] = "strict"
    seo_goal: str = "search + browse CTR"
    privacy_status: str = "private"


class ProductionPlan(BaseModel):
    project_id: str
    video_type: str = "documentary"
    duration_target: int = 720
    language: str = "hi-IN"
    estimated_scenes: int = 120
    research_depth: Literal["shallow", "standard", "deep"] = "deep"
    voice_required: bool = True
    images_required: bool = True
    motion_required: bool = True
    thumbnail_variants: int = 5
    subtitles_required: bool = True
    youtube_upload: bool = True
    word_count_target: int = 3600
    estimated_cost_usd: float = 0.0
    estimated_llm_calls: int = 0
    estimated_image_calls: int = 0
    notes: str = ""


# ── Research & facts ────────────────────────────────────────────────────

class SourceRef(BaseModel):
    url: str = ""
    title: str = ""
    source_type: str = "web"  # web | book | court_record | government | journalism
    publication_date: str = ""


class Claim(BaseModel):
    claim_id: str
    claim: str
    sources: list[SourceRef] = []
    evidence: str = ""
    confidence: float = 0.0  # 0..1
    corroboration: int = 0   # number of independent sources
    status: Literal["unverified", "verified", "rejected", "qualified"] = "unverified"
    qualification: str = ""  # how to phrase it if status == qualified


class ResearchDossier(BaseModel):
    topic: str
    summary: str = ""
    timeline: list[dict] = []       # [{date, event, source_urls}]
    people: list[dict] = []         # [{name, role, notes, deceased: bool|None}]
    places: list[str] = []
    claims: list[Claim] = []
    open_questions: list[str] = []
    documentation_strength: float = 0.0  # 0..100 — gate: is this topic documentable enough?


# ── Story & script ──────────────────────────────────────────────────────

class StoryBeat(BaseModel):
    beat_id: str
    section: str  # hook | background | inciting_incident | escalation | climax | resolution | significance | cta
    summary: str
    claims_used: list[str] = []
    target_words: int = 0
    tension_peak: bool = False


class StoryStructure(BaseModel):
    logline: str = ""
    beats: list[StoryBeat] = []


class Script(BaseModel):
    version: int = 1
    text: str
    word_count: int = 0
    language: str = "hi-IN"
    duration_estimates: dict[str, float] = {}  # {"50wpm": min, "100wpm": ..., "160wpm": ...}


class CritiqueReport(BaseModel):
    scores: dict[str, float] = {}   # factual_accuracy, story_quality, language, retention, brand_alignment
    overall: float = 0.0
    problems: list[str] = []
    required_fixes: list[str] = []
    unsupported_claims: list[str] = []
    verdict: Literal["approve", "revise", "reject"] = "revise"


# ── Storyboard & visuals ────────────────────────────────────────────────

class Scene(BaseModel):
    scene_id: str
    index: int
    narration_text: str
    start_seconds: float = 0.0
    duration_seconds: float = 4.0
    visual_description: str = ""
    characters: list[str] = []      # character_ids
    environment: str = ""
    camera: str = ""
    lighting: str = ""
    mood: str = ""
    action: str = ""
    transition: str = "cut"
    motion: str = "slow push-in"
    image_prompt: str = ""
    image_path: str = ""
    clip_path: str = ""
    audio_path: str = ""
    qa_score: float = 0.0
    source_reference: str = ""


class CharacterProfile(BaseModel):
    character_id: str
    name: str
    is_serving_head_of_state: bool = False
    age_appearance: str = ""
    appearance: str = ""
    clothing: str = ""
    facial_features: str = ""
    hairstyle: str = ""
    palette: str = ""
    prompt_fragment: str = ""       # canonical description injected into every prompt


class Storyboard(BaseModel):
    scenes: list[Scene] = []
    characters: list[CharacterProfile] = []
    total_duration: float = 0.0


# ── Audio ───────────────────────────────────────────────────────────────

class VoiceDirective(BaseModel):
    language: str = "hi-IN"
    tone: str = "serious"
    speed: float = 0.95
    emotion: str = "controlled"
    style: str = "documentary"
    provider_hint: str = ""
    voice_id: str = ""


class AudioSegment(BaseModel):
    scene_id: str
    path: str
    duration_seconds: float


# ── Packaging ───────────────────────────────────────────────────────────

class ThumbnailConcept(BaseModel):
    concept_id: str
    hindi_title: str
    characters: str
    background: str
    colors: str
    emotion_trigger: str
    image_prompt: str
    image_path: str = ""
    score: float = 0.0


class YouTubeMetadata(BaseModel):
    title: str = ""
    title_options: list[str] = []
    description: str = ""
    hashtags: list[str] = []
    tags: list[str] = []
    chapters: list[dict] = []       # [{start: "00:00", title}]
    category_id: str = "27"         # Education
    language: str = "hi"
    playlist: str = ""
    filename: str = ""


class AssetProvenance(BaseModel):
    asset_id: str
    path: str
    creator: str = "ai-video-agency"
    provider: str = ""
    source: str = "generated"
    license: str = "generated-content"
    generation_method: str = ""
    timestamp: str = ""


class QAReport(BaseModel):
    overall_score: float = 0.0
    content: float = 0.0
    visual: float = 0.0
    audio: float = 0.0
    editing: float = 0.0
    seo: float = 0.0
    policy: Literal["PASS", "FAIL", "PENDING"] = "PENDING"
    issues: list[dict] = []         # [{area, severity, description, repair_agent}]


class PublishResult(BaseModel):
    video_id: str
    video_url: str
    upload_time: str
    privacy_status: str
    thumbnail_status: str = ""
    caption_status: str = ""
