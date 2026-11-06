"""Consumidor de fleet.positions.v1.

Orden garantizado por lote:
  1. consume() de hasta BATCH_SIZE mensajes;
  2. mensajes inválidos → DLQ (esperando confirmación de Kafka);
  3. eventos válidos → una transacción en TimescaleDB (COMMIT);
  4. recién entonces commit síncrono de offsets.

Si el proceso cae entre 3 y 4, el lote se vuelve a leer y la deduplicación
por event_id hace que no cambie nada.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time

import psycopg
from confluent_kafka import Consumer, KafkaError, KafkaException, Message, Producer, TopicPartition
from pydantic import ValidationError

from fleet_contracts import DLQ_TOPIC, TOPIC, PositionEvent

from .persistence import InboundEvent, persist_batch

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("consumer")

KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9094")
GROUP_ID = os.getenv("KAFKA_GROUP_ID", "fleet-ingestion")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://fleet:fleet@localhost:5432/fleet")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "500"))
POLL_TIMEOUT_S = float(os.getenv("POLL_TIMEOUT_S", "1.0"))
SESSION_TIMEOUT_MS = int(os.getenv("KAFKA_SESSION_TIMEOUT_MS", "10000"))
# Solo para la prueba de caída: termina el proceso después del COMMIT en la
# base de datos y antes de confirmar el offset.
FAIL_AFTER_DB_COMMIT = os.getenv("FAIL_AFTER_DB_COMMIT", "0") == "1"
FAIL_EXIT_CODE = 3

_running = True


def _stop(signum, _frame):
    global _running
    log.info("Señal %s recibida; terminando después del lote actual", signum)
    _running = False


def _connect_db() -> psycopg.Connection:
    while True:
        try:
            conn = psycopg.connect(DATABASE_URL, autocommit=False)
            log.info("Conectado a TimescaleDB")
            return conn
        except psycopg.OperationalError as exc:
            log.warning("TimescaleDB no disponible: %s", exc)
            time.sleep(2)


def _send_to_dlq(producer: Producer, msg: Message, reason: str) -> None:
    errors: list[KafkaError] = []

    def on_delivery(err, _msg):
        if err is not None:
            errors.append(err)

    headers = list(msg.headers() or []) + [
        ("dlq_reason", reason[:1000].encode()),
        ("source_topic", msg.topic().encode()),
        ("source_partition", str(msg.partition()).encode()),
        ("source_offset", str(msg.offset()).encode()),
    ]
    producer.produce(DLQ_TOPIC, key=msg.key(), value=msg.value(), headers=headers,
                     on_delivery=on_delivery)
    remaining = producer.flush(10)
    if remaining or errors:
        # Sin confirmación de la DLQ no se avanza el offset.
        raise KafkaException(errors[0] if errors else "timeout al escribir en la DLQ")
    log.warning("Mensaje enviado a DLQ partition=%d offset=%d: %s",
                msg.partition(), msg.offset(), reason)


def _persist_with_retry(conn: psycopg.Connection, batch: list[InboundEvent]):
    delay = 1.0
    while True:
        try:
            return conn, persist_batch(conn, batch)
        except psycopg.OperationalError as exc:
            log.error("Error de conexión con la base de datos: %s", exc)
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            if not _running:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 30)
            conn = _connect_db()


def _commit_offsets(consumer: Consumer, messages: list[Message]) -> None:
    next_offsets: dict[tuple[str, int], int] = {}
    for msg in messages:
        key = (msg.topic(), msg.partition())
        next_offsets[key] = max(next_offsets.get(key, -1), msg.offset() + 1)
    offsets = [TopicPartition(t, p, o) for (t, p), o in next_offsets.items()]
    try:
        consumer.commit(offsets=offsets, asynchronous=False)
    except KafkaException as exc:
        # Ej.: perdimos la partición en un rebalanceo. Otro consumidor
        # releerá estos mensajes y la deduplicación los descartará.
        log.warning("No se pudo confirmar offsets %s: %s", offsets, exc)


def run() -> int:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    consumer = Consumer({
        "bootstrap.servers": KAFKA_BOOTSTRAP,
        "group.id": GROUP_ID,
        "enable.auto.commit": False,
        "enable.auto.offset.store": False,
        "auto.offset.reset": "earliest",
        "isolation.level": "read_committed",
        "session.timeout.ms": SESSION_TIMEOUT_MS,
        "heartbeat.interval.ms": max(1000, SESSION_TIMEOUT_MS // 3),
    })
    dlq_producer = Producer({
        "bootstrap.servers": KAFKA_BOOTSTRAP,
        "acks": "all",
        "enable.idempotence": True,
    })
    conn = _connect_db()
    consumer.subscribe([TOPIC])
    log.info("Suscrito a %s como grupo %s", TOPIC, GROUP_ID)

    try:
        while _running:
            messages = consumer.consume(num_messages=BATCH_SIZE, timeout=POLL_TIMEOUT_S)
            if not messages:
                continue

            processable: list[Message] = []
            batch: list[InboundEvent] = []
            for msg in messages:
                err = msg.error()
                if err is not None:
                    if err.code() != KafkaError._PARTITION_EOF:
                        log.error("Error de Kafka: %s", err)
                    continue
                processable.append(msg)
                try:
                    if msg.value() is None:
                        raise ValueError("mensaje sin valor")
                    event = PositionEvent.model_validate_json(msg.value())
                except (ValidationError, ValueError) as exc:
                    _send_to_dlq(dlq_producer, msg, str(exc))
                    continue
                batch.append(InboundEvent(event, msg.topic(), msg.partition(), msg.offset()))

            if not processable:
                continue

            if batch:
                conn, stats = _persist_with_retry(conn, batch)
                log.info(
                    "Lote confirmado en BD: nuevos=%d duplicados=%d conflictos_id=%d "
                    "posicion_actual_actualizada=%d",
                    stats.inserted, stats.duplicates, stats.id_conflicts, stats.current_updated,
                )

            if FAIL_AFTER_DB_COMMIT:
                log.error("FAIL_AFTER_DB_COMMIT=1: saliendo antes de confirmar offsets")
                logging.shutdown()
                os._exit(FAIL_EXIT_CODE)

            _commit_offsets(consumer, processable)
    finally:
        log.info("Cerrando consumidor")
        consumer.close()
        dlq_producer.flush(5)
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(run())
