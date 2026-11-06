"""Ruta de ingesta: valida el evento y responde solo después del ack de Kafka."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from aiokafka import AIOKafkaProducer
from aiokafka.errors import KafkaError
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from fleet_contracts import TOPIC, PositionEvent, check_not_in_future

from .settings import settings

log = logging.getLogger("api.ingest")
router = APIRouter()

# Variable de módulo para poder acortarla en pruebas.
SEND_TIMEOUT_S = settings.kafka_send_timeout_s


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

    headers = [
        ("received_at", datetime.now(timezone.utc).isoformat().encode()),
        ("producer", b"api"),
    ]
    try:
        # send_and_wait devuelve cuando el broker confirma con acks=all.
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
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Kafka no confirmó el mensaje; reintente con el mismo event_id",
        ) from exc

    log.info(
        "event_id=%s aceptado partition=%d offset=%d",
        event.event_id, metadata.partition, metadata.offset,
    )
    return Accepted(event_id=str(event.event_id))
