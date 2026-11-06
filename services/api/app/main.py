"""API de ingesta: valida el evento y responde solo después del ack de Kafka."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from aiokafka import AIOKafkaProducer
from aiokafka.errors import KafkaError
from fastapi import FastAPI, HTTPException, Request, status
from pydantic import BaseModel

from fleet_contracts import TOPIC, PositionEvent, check_not_in_future

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("api")

KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9094")
SEND_TIMEOUT_S = float(os.getenv("KAFKA_SEND_TIMEOUT_S", "5"))
STARTUP_RETRIES = int(os.getenv("KAFKA_STARTUP_RETRIES", "30"))


async def _start_producer() -> AIOKafkaProducer:
    for attempt in range(1, STARTUP_RETRIES + 1):
        producer = AIOKafkaProducer(
            bootstrap_servers=KAFKA_BOOTSTRAP,
            acks="all",
            enable_idempotence=True,
            linger_ms=5,
            request_timeout_ms=int(SEND_TIMEOUT_S * 1000),
        )
        try:
            await producer.start()
            log.info("Productor Kafka conectado a %s", KAFKA_BOOTSTRAP)
            return producer
        except KafkaError as exc:
            await producer.stop()
            log.warning("Kafka no disponible (intento %d): %s", attempt, exc)
            await asyncio.sleep(2)
    raise RuntimeError("No se pudo conectar a Kafka")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.producer = await _start_producer()
    try:
        yield
    finally:
        await app.state.producer.stop()


app = FastAPI(title="Fleet ingestion API", version="1.0.0", lifespan=lifespan)


class Accepted(BaseModel):
    event_id: str
    status: str = "accepted"


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post(
    "/v1/positions",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=Accepted,
    responses={503: {"description": "Kafka no confirmó el mensaje"}},
)
async def ingest_position(event: PositionEvent, request: Request) -> Accepted:
    try:
        check_not_in_future(event)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    producer: AIOKafkaProducer = request.app.state.producer
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
