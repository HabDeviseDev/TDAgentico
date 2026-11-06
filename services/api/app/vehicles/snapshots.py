"""Hub de snapshots: una lectura periódica compartida por instancia de API.

- Un único bucle lee vehicle_current_position cada `interval_s` segundos,
  sin importar cuántos navegadores estén conectados.
- Si el estado visible cambió, arma un snapshot completo, lo serializa una
  sola vez y lo entrega a cada suscriptor.
- Cada suscriptor tiene un buzón con como máximo un evento por tipo
  (`status`, `snapshot`). Un cliente lento solo recibe el más reciente: la
  memoria por cliente es constante y no crece con el atraso.
- Si la BD falla, se registra el error, se emite `status: degraded` y se
  conserva el último snapshot. La siguiente lectura correcta emite
  `status: ok` y un snapshot nuevo.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable

from .schemas import SnapshotPayload, StatusPayload, VehiclePosition, utcnow

log = logging.getLogger("api.snapshots")

Fetch = Callable[[], Awaitable[list[VehiclePosition]]]


@dataclass(frozen=True)
class ServerEvent:
    type: str          # nombre del evento SSE
    data: str          # JSON ya serializado (compartido entre clientes)
    id: str | None = None


class Subscription:
    """Buzón de un cliente: guarda solo el último evento de cada tipo."""

    def __init__(self) -> None:
        self._pending: dict[str, ServerEvent] = {}
        self._wake = asyncio.Event()

    def offer(self, event: ServerEvent) -> None:
        # Reinsertar mueve el tipo al final: se entregan en orden de llegada.
        self._pending.pop(event.type, None)
        self._pending[event.type] = event
        self._wake.set()

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    async def next(self, timeout: float) -> list[ServerEvent] | None:
        """Eventos pendientes, o None si pasó `timeout` sin novedades."""
        if not self._pending:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout)
            except asyncio.TimeoutError:
                return None
        self._wake.clear()
        events = list(self._pending.values())
        self._pending.clear()
        return events


class TooManySubscribers(Exception):
    pass


class SnapshotHub:
    def __init__(self, fetch: Fetch, interval_s: float, max_subscribers: int = 500,
                 clock: Callable[[], datetime] = utcnow):
        self._fetch = fetch
        self._interval_s = interval_s
        self._max_subscribers = max_subscribers
        self._clock = clock
        self._subscribers: set[Subscription] = set()
        self._fingerprint: str | None = None
        self._sequence = 0
        self._healthy: bool | None = None   # None = aún no se ha leído
        self._snapshot_event: ServerEvent | None = None
        self._status_event: ServerEvent | None = None
        self._last_snapshot_at: datetime | None = None

    # -- suscriptores ---------------------------------------------------------
    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def subscribe(self) -> Subscription:
        if len(self._subscribers) >= self._max_subscribers:
            raise TooManySubscribers()
        sub = Subscription()
        self._subscribers.add(sub)
        # Al conectar: estado de la lectura y snapshot completo actual.
        if self._status_event is not None:
            sub.offer(self._status_event)
        if self._snapshot_event is not None:
            sub.offer(self._snapshot_event)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        self._subscribers.discard(sub)

    def _broadcast(self, event: ServerEvent) -> None:
        for sub in self._subscribers:
            sub.offer(event)

    # -- lectura ----------------------------------------------------------------
    async def poll_once(self) -> None:
        try:
            vehicles = await self._fetch()
        except Exception as exc:  # noqa: BLE001 - cualquier fallo de BD/pool
            if self._healthy is not False:
                log.error("Fallo al leer posiciones actuales: %r", exc)
            else:
                log.debug("La BD sigue sin responder: %r", exc)
            # Se conserva el último snapshot; el siguiente ciclo reintenta.
            detail = ("La base de datos no respondió a tiempo" if isinstance(exc, TimeoutError)
                      else "No se pudo leer la base de datos")
            self._set_status(healthy=False, detail=detail)
            return

        recovered = self._healthy is False
        self._set_status(healthy=True)
        if recovered:
            log.info("Lectura de posiciones recuperada")

        now = self._clock()
        fingerprint = json.dumps([v.model_dump(mode="json") for v in vehicles],
                                 separators=(",", ":"))
        if fingerprint == self._fingerprint and not recovered:
            return
        self._fingerprint = fingerprint
        self._sequence += 1
        self._last_snapshot_at = now
        payload = SnapshotPayload(sequence=self._sequence, generated_at=now,
                                  count=len(vehicles), vehicles=vehicles)
        self._snapshot_event = ServerEvent("snapshot", payload.model_dump_json(),
                                           id=str(self._sequence))
        self._broadcast(self._snapshot_event)

    def _set_status(self, *, healthy: bool, detail: str | None = None) -> None:
        if self._healthy is healthy:
            return
        self._healthy = healthy
        payload = StatusPayload(status="ok" if healthy else "degraded",
                                generated_at=self._clock(), detail=detail,
                                last_snapshot_at=self._last_snapshot_at)
        self._status_event = ServerEvent("status", payload.model_dump_json())
        self._broadcast(self._status_event)

    async def run(self) -> None:
        log.info("Hub de snapshots iniciado (intervalo %.2fs)", self._interval_s)
        while True:
            try:
                await self.poll_once()
            except Exception:  # noqa: BLE001 - el bucle no debe morir
                log.exception("Error inesperado en el hub de snapshots")
            await asyncio.sleep(self._interval_s)
