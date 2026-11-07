"""Rutas de ingesta: validan cada evento y lo aceptan solo después del ack de Kafka.

- `POST /v1/positions`: un evento; `202` tras el ack, `503` si Kafka no confirmó.
- `POST /v1/positions/batch`: varios eventos con un resultado por evento.

Ambas usan la misma validación (`PositionEvent` + `check_not_in_future`) y la
misma publicación (`publish_event`).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Literal

from aiokafka import AIOKafkaProducer
from aiokafka.errors import KafkaError
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


async def start_producer() -> AIOKafkaProducer:
    for attempt in range(1, settings.kafka_startup_retries + 1):
        producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap,
            acks="all",
            enable_idempotence=True,
            linger_ms=5,
            request_timeout_ms=int(settings.kafka_send_timeout_s * 1000),
        )
        try:
            await producer.start()
            log.info("Productor Kafka conectado a %s", settings.kafka_bootstrap)
            return producer
        except KafkaError as exc:
            await producer.stop()
            log.warning("Kafka no disponible (intento %d): %s", attempt, exc)
            await asyncio.sleep(2)
    raise RuntimeError("No se pudo conectar a Kafka")


class Accepted(BaseModel):
    event_id: str
    status: str = "accepted"


def get_producer(request: Request) -> AIOKafkaProducer:
    """Dependencia separada para poder sustituir el productor en pruebas."""
    return request.app.state.producer


class PublishError(Exception):
    """Kafka no confirmó el mensaje: el cliente debe reintentar con el mismo event_id."""


async def publish_event(producer: AIOKafkaProducer, event: PositionEvent) -> None:
    """Publica y espera el ack de Kafka (acks=all) con límite de tiempo."""
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
    producer: AIOKafkaProducer = Depends(get_producer),
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
    producer: AIOKafkaProducer = Depends(get_producer),
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
