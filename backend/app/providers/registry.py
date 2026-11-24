"""Provider registry: lazy construction, ordered fallback chains, circuit breakers.

`call_with_fallback` is the single self-healing entry point every media agent uses.
It walks the chain, retrying transient failures with exponential backoff, opening a
circuit breaker on a provider that keeps failing, and raising ProviderUnavailableError
listing every credential that would unblock the capability if the whole chain is dead.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from app.core.errors import ProviderUnavailableError
from app.providers import image as image_mod
from app.providers import music as music_mod
from app.providers import notify as notify_mod
from app.providers import search as search_mod
from app.providers import tts as tts_mod
from app.providers import video as video_mod
from app.providers import youtube as youtube_mod

log = logging.getLogger("agency.registry")

T = TypeVar("T")

# Ordered preference per capability. First constructible provider wins; later ones
# are fallbacks. Order encodes quality preference, not availability.
CHAINS: dict[str, list[type]] = {
    "image": [image_mod.GeminiImageProvider, image_mod.OpenAIImageProvider,
              image_mod.StabilityImageProvider],
    "tts": [tts_mod.ElevenLabsTTS, tts_mod.EdgeTTS],
    "video": [video_mod.KlingVideoProvider, video_mod.KenBurnsVideoProvider],
    "music": [music_mod.LocalMusicLibrary],
    "youtube": [youtube_mod.YouTubeDataAPI],
    "notify": [notify_mod.TelegramNotifier, notify_mod.EmailNotifier, notify_mod.WebhookNotifier],
    "search": [search_mod.TavilySearch],
}

CIRCUIT_FAILURE_THRESHOLD = 4
CIRCUIT_COOLDOWN_SECONDS = 180.0


class _Breaker:
    def __init__(self) -> None:
        self.failures = 0
        self.opened_at = 0.0

    @property
    def open(self) -> bool:
        if self.failures < CIRCUIT_FAILURE_THRESHOLD:
            return False
        if time.monotonic() - self.opened_at > CIRCUIT_COOLDOWN_SECONDS:
            self.failures = 0  # half-open: let one attempt through
            return False
        return True

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= CIRCUIT_FAILURE_THRESHOLD:
            self.opened_at = time.monotonic()

    def record_success(self) -> None:
        self.failures = 0


_instances: dict[str, Any] = {}
_unavailable: dict[str, ProviderUnavailableError] = {}
_breakers: dict[str, _Breaker] = {}


def _breaker(key: str) -> _Breaker:
    return _breakers.setdefault(key, _Breaker())


def _instantiate(cls: type) -> Any | None:
    key = f"{cls.__module__}.{cls.__name__}"
    if key in _instances:
        return _instances[key]
    if key in _unavailable:
        return None
    try:
        _instances[key] = cls()
        return _instances[key]
    except ProviderUnavailableError as e:
        log.info("provider %s unavailable: %s", cls.__name__, e.reason)
        _unavailable[key] = e
        return None
    except Exception as e:
        log.warning("provider %s failed to construct: %s", cls.__name__, e)
        _unavailable[key] = ProviderUnavailableError(cls.__name__, str(e))
        return None


def available_providers(capability: str) -> list[Any]:
    """Constructible providers for a capability, in preference order."""
    out = []
    for cls in CHAINS.get(capability, []):
        inst = _instantiate(cls)
        if inst is not None and not _breaker(inst.name).open:
            out.append(inst)
    return out


def missing_credentials(capability: str) -> list[str]:
    missing: list[str] = []
    for cls in CHAINS.get(capability, []):
        err = _unavailable.get(f"{cls.__module__}.{cls.__name__}")
        if err:
            missing.extend(err.missing)
    return sorted(set(missing))


def get_provider(capability: str) -> Any:
    """First healthy provider, or raise with the exact credentials needed."""
    providers = available_providers(capability)
    if not providers:
        raise ProviderUnavailableError(
            capability, "no provider available for this capability",
            missing=missing_credentials(capability))
    return providers[0]


async def call_with_fallback(
    capability: str,
    operation: Callable[[Any], Awaitable[T]],
    *,
    attempts_per_provider: int = 3,
    on_provider_used: Callable[[str], None] | None = None,
) -> T:
    """Run `operation` against each provider in the chain until one succeeds.

    Transient failures are retried with jittered exponential backoff before moving to
    the next provider. Raises ProviderUnavailableError if the entire chain fails.
    """
    providers = available_providers(capability)
    if not providers:
        raise ProviderUnavailableError(
            capability, "no provider available",
            missing=missing_credentials(capability))

    errors: list[str] = []
    for provider in providers:
        breaker = _breaker(provider.name)
        if breaker.open:
            errors.append(f"{provider.name}: circuit open")
            continue
        for attempt in range(1, attempts_per_provider + 1):
            try:
                result = await operation(provider)
                breaker.record_success()
                if on_provider_used:
                    on_provider_used(provider.name)
                return result
            except ProviderUnavailableError as e:
                breaker.record_failure()
                errors.append(f"{provider.name}: {e.reason}")
                log.warning("provider %s attempt %d failed: %s", provider.name, attempt, e.reason)
            except Exception as e:
                breaker.record_failure()
                errors.append(f"{provider.name}: {type(e).__name__}: {e}")
                log.warning("provider %s attempt %d errored: %s", provider.name, attempt, e)
            if attempt < attempts_per_provider:
                await asyncio.sleep(min(2 ** attempt + random.uniform(0, 1), 30))

    raise ProviderUnavailableError(
        capability, "all providers failed: " + " | ".join(errors[:6]),
        missing=missing_credentials(capability))


def capability_report() -> dict:
    """Dashboard view of what the agency can currently do."""
    report = {}
    for capability in CHAINS:
        providers = available_providers(capability)
        report[capability] = {
            "available": [p.name for p in providers],
            "missing_credentials": missing_credentials(capability),
            "ready": bool(providers),
        }
    return report


def reset() -> None:
    """Clear caches — used by tests and after credential changes."""
    _instances.clear()
    _unavailable.clear()
    _breakers.clear()
