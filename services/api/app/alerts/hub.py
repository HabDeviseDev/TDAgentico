"""Hub de alertas: una evaluación periódica compartida por instancia.

- Un único bucle evalúa cada `interval_s` segundos a partir del historial
  confirmado, sin importar cuántos clientes estén conectados.
- Se reevalúa aunque la posición actual no cambie: un evento atrasado o el
  paso del tiempo (muestra desactualizada) cambian el resultado.
- Solo se difunde un evento `alerts` si el resultado cambió (se ignora la
  hora de evaluación) o al recuperarse de un fallo.
- Si la evaluación falla: `alerts_status: degraded`, se conserva el último
  resultado y el siguiente ciclo reintenta. Nunca se publica una lista vacía
  en lugar de un resultado que no se pudo calcular.
- Los eventos van a los mismos buzones acotados de los clientes SSE
  (`Subscription`, uno por tipo de evento).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Awaitable, Callable

from ..vehicles.schemas import utcnow
from ..vehicles.snapshots import ServerEvent, Subscription, TooManySubscribers
from .rules import FleetEvaluation
from .schemas import AlertsPayload, AlertsResponse, AlertsStatusPayload

log = logging.getLogger("api.alerts")

Evaluate = Callable[[], Awaitable[FleetEvaluation]]
# Campos que cambian en cada evaluación aunque el resultado sea el mismo.
_VOLATILE = ("evaluated_at", "window_start")


class AlertHub:
    def __init__(self, evaluate: Evaluate, interval_s: float, *, max_vehicles: int,
                 max_rows: int, max_subscribers: int = 500,
                 clock: Callable[[], datetime] = utcnow):
        self._evaluate = evaluate
        self._interval_s = interval_s
        self._max_vehicles = max_vehicles
        self._max_rows = max_rows
        self._max_subscribers = max_subscribers
        self._clock = clock
        self._subscribers: set[Subscription] = set()
        self._fingerprint: str | None = None
        self._sequence = 0
        self._healthy: bool | None = None
        self._alerts_event: ServerEvent | None = None
        self._status_event: ServerEvent | None = None
        self._last_evaluation_at: datetime | None = None

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def subscribe(self, sub: Subscription | None = None) -> Subscription:
        if len(self._subscribers) >= self._max_subscribers:
            raise TooManySubscribers()
        sub = sub or Subscription()
        self._subscribers.add(sub)
        # Al conectar o reconectar: estado y resultado completo actuales.
        if self._status_event is not None:
            sub.offer(self._status_event)
        if self._alerts_event is not None:
            sub.offer(self._alerts_event)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        self._subscribers.discard(sub)

    def _broadcast(self, event: ServerEvent) -> None:
        for sub in self._subscribers:
            sub.offer(event)

    async def poll_once(self) -> None:
        try:
            evaluation = await self._evaluate()
        except Exception as exc:  # noqa: BLE001 - BD, pool o timeout
            if self._healthy is not False:
                log.error("Fallo al evaluar alertas: %r", exc)
            detail = ("La base de datos no respondió a tiempo" if isinstance(exc, TimeoutError)
                      else "No se pudo evaluar las alertas")
            self._set_status(healthy=False, detail=detail)
            return

        recovered = self._healthy is False
        self._last_evaluation_at = evaluation.evaluated_at
        self._set_status(healthy=True)
        if recovered:
            log.info("Evaluación de alertas recuperada")

        result = AlertsResponse.of(evaluation, self._max_vehicles, self._max_rows)
        stable = result.model_dump(mode="json", exclude=set(_VOLATILE))
        fingerprint = json.dumps(stable, sort_keys=True, separators=(",", ":"))
        if fingerprint == self._fingerprint and not recovered:
            return
        self._fingerprint = fingerprint
        self._sequence += 1
        payload = AlertsPayload(sequence=self._sequence, **result.model_dump())
        self._alerts_event = ServerEvent("alerts", payload.model_dump_json(),
                                         id=f"a{self._sequence}")
        self._broadcast(self._alerts_event)

    def _set_status(self, *, healthy: bool, detail: str | None = None) -> None:
        if self._healthy is healthy:
            return
        self._healthy = healthy
        payload = AlertsStatusPayload(status="ok" if healthy else "degraded",
                                      generated_at=self._clock(), detail=detail,
                                      last_evaluation_at=self._last_evaluation_at)
        self._status_event = ServerEvent("alerts_status", payload.model_dump_json())
        self._broadcast(self._status_event)

    async def run(self) -> None:
        log.info("Hub de alertas iniciado (intervalo %.2fs)", self._interval_s)
        while True:
            try:
                await self.poll_once()
            except Exception:  # noqa: BLE001 - el bucle no debe morir
                log.exception("Error inesperado en el hub de alertas")
            await asyncio.sleep(self._interval_s)
