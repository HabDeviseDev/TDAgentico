"""Formato SSE y generador de eventos por cliente."""

from __future__ import annotations

import json
from typing import AsyncIterator

from .schemas import to_utc_iso, utcnow
from .snapshots import ServerEvent, SnapshotHub

# El navegador reintenta a los 3 s si se corta la conexión.
RETRY_MS = 3000


def format_event(event: ServerEvent) -> str:
    lines = [f"event: {event.type}"]
    if event.id is not None:
        lines.append(f"id: {event.id}")
    lines.extend(f"data: {line}" for line in event.data.splitlines() or [""])
    return "\n".join(lines) + "\n\n"


def heartbeat_event() -> ServerEvent:
    return ServerEvent("heartbeat", json.dumps({"type": "heartbeat",
                                                "generated_at": to_utc_iso(utcnow())},
                                               separators=(",", ":")))


async def client_stream(hub: SnapshotHub, heartbeat_s: float) -> AsyncIterator[str]:
    """Eventos de un cliente. La suscripción se libera al cerrarse el generador,
    también cuando Starlette lo cancela porque el cliente se desconectó."""
    sub = hub.subscribe()
    try:
        yield f"retry: {RETRY_MS}\n\n"
        while True:
            events = await sub.next(timeout=heartbeat_s)
            if events is None:
                yield format_event(heartbeat_event())
                continue
            for event in events:
                yield format_event(event)
    finally:
        hub.unsubscribe(sub)
