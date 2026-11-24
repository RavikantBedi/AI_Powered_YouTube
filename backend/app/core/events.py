"""Event bus: async in-process pub/sub + durable persistence to the events table.
Agents and the dashboard subscribe; every event is also written to the DB so the
production timeline is auditable and the UI can poll it."""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable

from app.db.base import get_session_factory
from app.db.models import EventRecord

log = logging.getLogger("agency.events")

Handler = Callable[[str, str, dict], Awaitable[None]]


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: Handler) -> None:
        self._subs[event_type].append(handler)

    async def emit(self, project_id: str, event_type: str, payload: dict | None = None) -> None:
        payload = payload or {}
        log.info("event %s project=%s %s", event_type, project_id, payload)
        try:
            async with get_session_factory()() as session:
                session.add(EventRecord(project_id=project_id, event_type=event_type, payload=payload))
                await session.commit()
        except Exception:  # persistence must never break the pipeline
            log.exception("failed to persist event %s", event_type)
        for handler in list(self._subs.get(event_type, [])) + list(self._subs.get("*", [])):
            try:
                await handler(project_id, event_type, payload)
            except Exception:
                log.exception("event handler failed for %s", event_type)


bus = EventBus()


async def emit(project_id: str, event_type: str, payload: dict | None = None) -> None:
    await bus.emit(project_id, event_type, payload)


def fire_and_forget(project_id: str, event_type: str, payload: dict | None = None) -> None:
    asyncio.get_running_loop().create_task(bus.emit(project_id, event_type, payload))
