"""Model router: chooses an LLM tier per task and escalates on repeated failure.

Tiers (env-overridable):
  heavy    — research, script writing, final QA, fact verification
  standard — storyboard, visual direction, SEO, critics
  light    — classification, formatting, filename generation
"""
from __future__ import annotations

from app.config import get_settings

TASK_TIERS: dict[str, str] = {
    "requirements": "standard",
    "planning": "standard",
    "research": "heavy",
    "fact_check": "heavy",
    "story": "heavy",
    "script": "heavy",
    "critic": "heavy",
    "storyboard": "standard",
    "characters": "standard",
    "visual_direction": "standard",
    "image_qa": "standard",
    "voice_direction": "light",
    "seo": "standard",
    "thumbnail": "standard",
    "policy": "heavy",
    "final_qa": "heavy",
    "classification": "light",
    "formatting": "light",
    "factory": "heavy",
}

_ESCALATION = ["light", "standard", "heavy"]


class ModelRouter:
    def __init__(self) -> None:
        s = get_settings()
        self.tier_models = {"heavy": s.model_heavy, "standard": s.model_standard, "light": s.model_light}

    def model_for(self, task: str, attempt: int = 1) -> str:
        tier = TASK_TIERS.get(task, "standard")
        # Escalation ladder: attempt 3+ moves one tier up (spec §54: "change model").
        if attempt >= 3:
            idx = min(_ESCALATION.index(tier) + 1, len(_ESCALATION) - 1)
            tier = _ESCALATION[idx]
        return self.tier_models[tier]


router = ModelRouter()
