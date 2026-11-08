"""Rutas de ingesta: validan cada evento y lo aceptan solo después del ack de Kafka.

- `POST /v1/positions`: un evento; `202` tras el ack, `503` si Kafka no confirmó.
- `POST /v1/positions/batch`: varios eventos con un resultado por evento.

Ambas usan la misma validación (`PositionEvent` + `check_not_in_future`) y la
misma publicación (`publish_event`).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Literal

from aiokafka import AIOKafkaProducer
from aiokafka.errors import (
    InvalidProducerEpoch,
    InvalidProducerIdMapping,
    InvalidTxnState,
    KafkaError,
    OutOfOrderSequenceNumber,
    ProducerFenced,
    UnknownProducerId,
)
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ValidationError

from fleet_contracts import TOPIC, PositionEvent, check_not_in_future

from .settings import settings

log = logging.getLogger("api.ingest")
router = APIRouter()

# Variables de módulo para poder acortarlas en pruebas.
SEND_TIMEOUT_S = settings.kafka_send_timeout_s
BATCH_MAX_EVENTS = settings.batch_max_events
BATCH_MAX_BYTES = settings.batch_max_bytes
# Reemplazo del productor tras un error fatal (ver ResilientProducer).
PRODUCER_START_TIMEOUT_S = max(10.0, settings.kafka_send_timeout_s * 2)
PRODUCER_STOP_TIMEOUT_S = 10.0
PRODUCER_RECREATE_MIN_INTERVAL_S = 3.0


def _new_producer() -> AIOKafkaProducer:
    return AIOKafkaProducer(
        bootstrap_servers=settings.kafka_bootstrap,
        acks="all",
        enable_idempotence=True,
        linger_ms=5,
        request_timeout_ms=int(settings.kafka_send_timeout_s * 1000),
    )


async def _start_once(timeout_s: float) -> AIOKafkaProducer:
    """Crea y arranca un productor; si no arranca a tiempo, lo cierra y propaga el error."""
    producer = _new_producer()
    try:
        await asyncio.wait_for(producer.start(), timeout=timeout_s)
    except BaseException:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(producer.stop(), timeout=timeout_s)
        raise
    return producer


async def _start_with_retries() -> AIOKafkaProducer:
    for attempt in range(1, settings.kafka_startup_retries + 1):
        try:
            producer = await _start_once(PRODUCER_START_TIMEOUT_S)
            log.info("Productor Kafka conectado a %s", settings.kafka_bootstrap)
            return producer
        except (KafkaError, asyncio.TimeoutError) as exc:
            log.warning("Kafka no disponible (intento %d): %r", attempt, exc)
            await asyncio.sleep(2)
    raise RuntimeError("No se pudo conectar a Kafka")


# Errores tras los que aiokafka no vuelve a enviar con ese productor: su tarea
# interna termina y el acumulador repite la misma excepción en cada envío.
FATAL_PRODUCER_ERRORS: tuple[type[BaseException], ...] = (
    OutOfOrderSequenceNumber,
    UnknownProducerId,
    ProducerFenced,
    InvalidProducerEpoch,
    InvalidProducerIdMapping,
    InvalidTxnState,
)


def _sender_is_dead(producer: Any) -> bool:
    """True si terminó la tarea de envío interna de aiokafka (el productor ya no envía).
    Lee atributos privados de aiokafka 0.12 con cuidado: un doble no los tiene."""
    sender = getattr(producer, "_sender", None)
    task = getattr(sender, "sender_task", None)
    return isinstance(task, asyncio.Future) and task.done()


async def _force_close(producer: Any, timeout_s: float) -> None:
    """Cierre sin vaciar el acumulador. `stop()` espera a que se confirmen los
    lotes pendientes, y con un productor inservible eso no ocurre: al vencer,
    su tarea de envío quedaba viva ("Task was destroyed but it is pending").
    Aquí se cancela esa tarea y se cierran las conexiones (atributos de
    aiokafka 0.12; un doble puede no tenerlos)."""
    for part in (getattr(producer, "_sender", None), getattr(producer, "client", None)):
        close = getattr(part, "close", None)
        if close is None:
            continue
        try:
            await asyncio.wait_for(close(), timeout=timeout_s)
        except Exception as exc:
            log.warning("Cierre forzado incompleto (%s): %r", type(part).__name__, exc)


def needs_replacement(producer: Any, exc: BaseException) -> bool:
    return isinstance(exc, FATAL_PRODUCER_ERRORS) or _sender_is_dead(producer)


class ResilientProducer:
    """Productor Kafka que se reemplaza solo cuando queda inservible.

    Tras reiniciar Kafka, el productor idempotente de aiokafka puede recibir
    `OutOfOrderSequenceNumber` (u otro error fatal) y desde entonces fallar en
    cada envío. Al detectarlo se programa UN reemplazo en segundo plano
    (candado + generación): se arranca un productor nuevo y, si arranca, se
    cambia por el anterior y el anterior se cierra. El envío que falló sigue
    fallando (503 o `retryable`); el cliente reenvía con el mismo event_id y
    ese intento ya usa el productor nuevo. Si Kafka aún no responde, se
    conserva el anterior y el siguiente error fatal lo vuelve a intentar
    (como mucho un intento cada `min_interval_s`).
    """

    def __init__(self, factory: Callable[[], Awaitable[Any]], initial: Any, *,
                 min_interval_s: float | None = None, stop_timeout_s: float | None = None):
        self._factory = factory
        self._producer = initial
        self._generation = 0
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._last_attempt = float("-inf")
        self._min_interval_s = (PRODUCER_RECREATE_MIN_INTERVAL_S
                                if min_interval_s is None else min_interval_s)
        self._stop_timeout_s = (PRODUCER_STOP_TIMEOUT_S
                                if stop_timeout_s is None else stop_timeout_s)
        self._closed = False
        self.replacements = 0

    @property
    def current(self) -> Any:
        return self._producer

    async def send_and_wait(self, topic: str, value=None, key=None, headers=None):
        producer, generation = self._producer, self._generation
        try:
            return await producer.send_and_wait(topic, value=value, key=key, headers=headers)
        except KafkaError as exc:
            if needs_replacement(producer, exc):
                self._schedule_replacement(generation, exc)
            raise

    def _schedule_replacement(self, generation: int, reason: BaseException) -> None:
        if self._closed or generation != self._generation:
            return  # ese productor ya se reemplazó
        if self._task is not None and not self._task.done():
            return  # un solo reemplazo a la vez
        self._task = asyncio.create_task(self._replace(generation, reason),
                                         name="kafka-producer-replace")

    async def _replace(self, generation: int, reason: BaseException) -> None:
        async with self._lock:
            if self._closed or generation != self._generation:
                return
            loop = asyncio.get_running_loop()
            wait = self._last_attempt + self._min_interval_s - loop.time()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_attempt = loop.time()
            log.warning("Productor Kafka inservible (%r); creando uno nuevo", reason)
            try:
                new = await self._factory()
            except Exception as exc:  # Kafka aún no responde: se reintenta con el siguiente error
                log.warning("No se pudo crear el productor nuevo: %r", exc)
                return
            if self._closed:
                old = new
            else:
                old, self._producer = self._producer, new
                self._generation += 1
                self.replacements += 1
                log.info("Productor Kafka reemplazado (reemplazo n.º %d)", self.replacements)
        await self._stop_quietly(old)

    async def _stop_quietly(self, producer: Any) -> None:
        try:
            await asyncio.wait_for(producer.stop(), timeout=self._stop_timeout_s)
            return
        except Exception as exc:
            log.warning("El productor anterior no cerró limpio (%r); cierre forzado", exc)
        await _force_close(producer, self._stop_timeout_s)

    async def stop(self) -> None:
        self._closed = True
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        await self._stop_quietly(self._producer)


async def start_producer() -> ResilientProducer:
    initial = await _start_with_retries()
    return ResilientProducer(lambda: _start_once(PRODUCER_START_TIMEOUT_S), initial)


class Accepted(BaseModel):
    event_id: str
    status: str = "accepted"


def get_producer(request: Request) -> ResilientProducer:
    """Dependencia separada para poder sustituir el productor en pruebas."""
    return request.app.state.producer


class PublishError(Exception):
    """Kafka no confirmó el mensaje: el cliente debe reintentar con el mismo event_id."""


async def publish_event(producer: ResilientProducer, event: PositionEvent) -> None:
    """Publica y espera el ack de Kafka (acks=all) con límite de tiempo.
    Si el error deja inservible al productor, ResilientProducer programa su
    reemplazo; aquí solo se informa que no hubo ack (503 / retryable)."""
    headers = [
        ("received_at", datetime.now(timezone.utc).isoformat().encode()),
        ("producer", b"api"),
    ]
    try:
        metadata = await asyncio.wait_for(
            producer.send_and_wait(
                TOPIC,
                value=event.canonical_json(),
                key=event.vehicle_id.encode("utf-8"),
                headers=headers,
            ),
            timeout=SEND_TIMEOUT_S,
        )
    except (KafkaError, asyncio.TimeoutError) as exc:
        log.error("Kafka no confirmó event_id=%s: %r", event.event_id, exc)
        raise PublishError(str(exc) or type(exc).__name__) from exc
    log.info("event_id=%s aceptado partition=%d offset=%d",
             event.event_id, metadata.partition, metadata.offset)


@router.post(
    "/v1/positions",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=Accepted,
    responses={503: {"description": "Kafka no confirmó el mensaje"}},
)
async def ingest_position(
    event: PositionEvent,
    producer: ResilientProducer = Depends(get_producer),
) -> Accepted:
    try:
        check_not_in_future(event)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    try:
        await publish_event(producer, event)
    except PublishError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Kafka no confirmó el mensaje; reintente con el mismo event_id",
        ) from exc
    return Accepted(event_id=str(event.event_id))


# -- lotes -----------------------------------------------------------------------------

class BatchItemResult(BaseModel):
    index: int
    event_id: str | None
    status: Literal["accepted", "rejected", "retryable"]
    error: str | None = None


class BatchResponse(BaseModel):
    received: int
    accepted: int
    rejected: int
    retryable: int
    results: list[BatchItemResult]


def _raw_event_id(item: Any) -> str | None:
    """event_id tal como llegó (también si el evento es inválido), acotado."""
    if isinstance(item, dict) and isinstance(item.get("event_id"), str):
        return item["event_id"][:64]
    return None


def _validation_message(exc: ValidationError) -> str:
    parts = []
    for e in exc.errors()[:5]:
        loc = ".".join(str(p) for p in e["loc"]) or "evento"
        parts.append(f"{loc}: {e['msg']}")
    return "; ".join(parts)[:300]


def _validate_items(items: list[Any]) -> list[PositionEvent | str]:
    """Para cada elemento, el evento validado o el motivo del rechazo.
    Corre en un hilo: no bloquea el event loop con lotes grandes."""
    now = datetime.now(timezone.utc)
    out: list[PositionEvent | str] = []
    for item in items:
        if not isinstance(item, dict):
            out.append("el evento debe ser un objeto JSON")
            continue
        try:
            event = PositionEvent.model_validate(item)
            check_not_in_future(event, now)
        except ValidationError as exc:
            out.append(_validation_message(exc))
            continue
        except ValueError as exc:
            out.append(str(exc))
            continue
        out.append(event)
    return out


async def _read_limited_body(request: Request, max_bytes: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            f"El lote supera {max_bytes} bytes")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > max_bytes:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                                f"El lote supera {max_bytes} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post(
    "/v1/positions/batch",
    response_model=BatchResponse,
    responses={400: {"description": "JSON inválido"},
               413: {"description": "Lote demasiado grande (bytes o eventos)"},
               422: {"description": "Cuerpo sin la forma {\"events\": [...]}"}},
)
async def ingest_batch(
    request: Request,
    producer: ResilientProducer = Depends(get_producer),
) -> BatchResponse:
    """Un resultado por evento, en el orden de entrada (`index`):
    - accepted: Kafka confirmó la publicación (acks=all).
    - rejected: el evento no es válido; reenviarlo igual no servirá.
    - retryable: no se pudo confirmar la publicación; reenviar con el MISMO
      event_id y contenido (la deduplicación del consumidor evita duplicados).
    Un lote puede quedar parcialmente publicado.
    """
    raw = await _read_limited_body(request, BATCH_MAX_BYTES)
    try:
        body = await asyncio.to_thread(json.loads, raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "El cuerpo no es JSON válido") from exc
    if not isinstance(body, dict) or not isinstance(body.get("events"), list):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            'El cuerpo debe ser {"events": [...]}')
    items = body["events"]
    if not items:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "El lote no tiene eventos")
    if len(items) > BATCH_MAX_EVENTS:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            f"El lote tiene {len(items)} eventos; máximo {BATCH_MAX_EVENTS}")

    validated = await asyncio.to_thread(_validate_items, items)

    async def publish(event: PositionEvent) -> str | None:
        try:
            await publish_event(producer, event)
            return None
        except PublishError as exc:
            return f"Kafka no confirmó la publicación: {exc}"[:300]

    to_publish = [(i, v) for i, v in enumerate(validated) if isinstance(v, PositionEvent)]
    # Todos a la vez: el productor los agrupa (linger_ms) y cada uno espera su ack.
    outcomes = await asyncio.gather(*(publish(e) for _, e in to_publish))
    publish_error = {i: err for (i, _), err in zip(to_publish, outcomes)}

    results: list[BatchItemResult] = []
    for i, v in enumerate(validated):
        if isinstance(v, str):
            results.append(BatchItemResult(index=i, event_id=_raw_event_id(items[i]),
                                           status="rejected", error=v))
        elif publish_error[i] is None:
            results.append(BatchItemResult(index=i, event_id=str(v.event_id), status="accepted"))
        else:
            results.append(BatchItemResult(index=i, event_id=str(v.event_id), status="retryable",
                                           error=publish_error[i]))
    counts = {s: sum(r.status == s for r in results) for s in ("accepted", "rejected", "retryable")}
    log.info("lote de %d eventos: %s", len(items), counts)
    return BatchResponse(received=len(items), **counts, results=results)
