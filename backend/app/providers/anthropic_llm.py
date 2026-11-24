"""Anthropic adapter — the system's reasoning engine.

Uses the official `anthropic` SDK. Streaming is used for long generations (scripts can
run tens of thousands of tokens). Research uses the server-side web search tool
(web_search_20260209) so deep research needs no extra search vendor.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path

import anthropic

from app.config import get_settings
from app.core.costs import llm_cost
from app.core.errors import ProviderUnavailableError
from app.providers.base import LLMProvider, LLMResult

log = logging.getLogger("agency.llm")


class AnthropicLLM(LLMProvider):
    name = "anthropic"

    def __init__(self) -> None:
        s = get_settings()
        if not s.anthropic_api_key:
            raise ProviderUnavailableError("anthropic", "no API key configured",
                                           missing=["ANTHROPIC_API_KEY"])
        self.client = anthropic.AsyncAnthropic(api_key=s.anthropic_api_key)

    async def complete(self, *, model: str, system: str, user: str,
                       max_tokens: int = 16000, effort: str = "high") -> LLMResult:
        try:
            async with self.client.messages.stream(
                model=model,
                max_tokens=max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                thinking={"type": "adaptive"},
                output_config={"effort": effort},
                messages=[{"role": "user", "content": user}],
            ) as stream:
                response = await stream.get_final_message()
        except anthropic.RateLimitError as e:
            raise ProviderUnavailableError("anthropic", f"rate limited: {e.message}") from e
        except anthropic.AuthenticationError as e:
            raise ProviderUnavailableError("anthropic", "invalid API key",
                                           missing=["ANTHROPIC_API_KEY"]) from e
        except anthropic.APIStatusError as e:
            if e.status_code >= 500:
                raise ProviderUnavailableError("anthropic", f"server error {e.status_code}") from e
            raise
        except anthropic.APIConnectionError as e:
            raise ProviderUnavailableError("anthropic", "network error") from e
        return self._to_result(response, model)

    async def complete_with_web_search(self, *, model: str, system: str, user: str,
                                       max_tokens: int = 16000, max_searches: int = 12) -> LLMResult:
        tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": max_searches}]
        messages: list[dict] = [{"role": "user", "content": user}]
        # Server tools may pause the turn on long research; resume until done.
        for _ in range(8):
            async with self.client.messages.stream(
                model=model,
                max_tokens=max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                thinking={"type": "adaptive"},
                tools=tools,
                messages=messages,
            ) as stream:
                response = await stream.get_final_message()
            if response.stop_reason != "pause_turn":
                break
            messages.append({"role": "assistant", "content": response.content})
        return self._to_result(response, model)

    async def complete_with_images(self, *, model: str, system: str, user: str,
                                   images: list[Path], max_tokens: int = 8000,
                                   effort: str = "high") -> LLMResult:
        """Vision call used by the QA agents to actually look at generated media."""
        content: list[dict] = []
        for path in images[:20]:
            data = Path(path).read_bytes()
            media_type = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                          "webp": "image/webp"}.get(Path(path).suffix.lstrip(".").lower(), "image/png")
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": media_type,
                "data": base64.standard_b64encode(data).decode(),
            }})
        content.append({"type": "text", "text": user})
        try:
            async with self.client.messages.stream(
                model=model,
                max_tokens=max_tokens,
                system=system,
                thinking={"type": "adaptive"},
                output_config={"effort": effort},
                messages=[{"role": "user", "content": content}],
            ) as stream:
                response = await stream.get_final_message()
        except anthropic.RateLimitError as e:
            raise ProviderUnavailableError("anthropic", f"rate limited: {e.message}") from e
        except anthropic.APIStatusError as e:
            if e.status_code >= 500:
                raise ProviderUnavailableError("anthropic", f"server error {e.status_code}") from e
            raise
        except anthropic.APIConnectionError as e:
            raise ProviderUnavailableError("anthropic", "network error") from e
        return self._to_result(response, model)

    def _to_result(self, response, model: str) -> LLMResult:
        text = "".join(b.text for b in response.content if b.type == "text")
        citations: list[dict] = []
        for block in response.content:
            for cit in (getattr(block, "citations", None) or []):
                url = getattr(cit, "url", "")
                if url:
                    citations.append({"url": url, "title": getattr(cit, "title", "") or ""})
        usage = response.usage
        return LLMResult(
            text=text,
            model=model,
            input_tokens=usage.input_tokens + (usage.cache_read_input_tokens or 0),
            output_tokens=usage.output_tokens,
            cost_usd=llm_cost(model, usage.input_tokens, usage.output_tokens),
            stop_reason=response.stop_reason or "",
            citations=citations,
        )


# ── JSON extraction helper used by every structured agent ───────────────

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> dict | list:
    """Robustly pull a JSON object/array out of a model response."""
    candidates = [m.group(1) for m in _JSON_FENCE.finditer(text)]
    candidates.append(text)
    first_brace = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if first_brace >= 0:
        candidates.append(text[first_brace:])
    for cand in candidates:
        cand = cand.strip()
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            # try trimming trailing prose after the last closing bracket
            for closer in ("}", "]"):
                last = cand.rfind(closer)
                if last > 0:
                    try:
                        return json.loads(cand[: last + 1])
                    except json.JSONDecodeError:
                        continue
    raise ValueError("no valid JSON found in model response")


_llm_singleton: AnthropicLLM | None = None


def get_llm() -> AnthropicLLM:
    global _llm_singleton
    if _llm_singleton is None:
        _llm_singleton = AnthropicLLM()
    return _llm_singleton
