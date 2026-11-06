"""Mensaje inválido publicado directamente en Kafka (sin pasar por la API).

Requiere el stack real: Kafka, consumidor y TimescaleDB.
"""

from __future__ import annotations

import time
import uuid

from confluent_kafka import Consumer, Producer, TopicPartition

from conftest import GROUP_ID, KAFKA_BOOTSTRAP, WAIT_TIMEOUT_S, wait_for_consumer_caught_up
from fleet_contracts import DLQ_TOPIC, TOPIC


def produce_raw(value: bytes, key: bytes) -> tuple[int, int]:
    delivered = {}

    def on_delivery(err, msg):
        assert err is None, err
        delivered["partition"], delivered["offset"] = msg.partition(), msg.offset()

    producer = Producer({"bootstrap.servers": KAFKA_BOOTSTRAP, "acks": "all"})
    producer.produce(TOPIC, key=key, value=value, on_delivery=on_delivery)
    assert producer.flush(10) == 0
    return delivered["partition"], delivered["offset"]


def committed_offset(partition: int) -> int:
    probe = Consumer({"bootstrap.servers": KAFKA_BOOTSTRAP, "group.id": GROUP_ID,
                      "enable.auto.commit": False})
    try:
        return probe.committed([TopicPartition(TOPIC, partition)], timeout=5)[0].offset
    finally:
        probe.close()


def find_in_dlq(partition: int, offset: int) -> dict | None:
    reader = Consumer({
        "bootstrap.servers": KAFKA_BOOTSTRAP,
        "group.id": f"dlq-reader-{uuid.uuid4()}",
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,
    })
    reader.subscribe([DLQ_TOPIC])
    deadline = time.monotonic() + WAIT_TIMEOUT_S
    try:
        while time.monotonic() < deadline:
            msg = reader.poll(1.0)
            if msg is None or msg.error():
                continue
            headers = {k: v for k, v in (msg.headers() or [])}
            if (headers.get("source_topic") == TOPIC.encode()
                    and headers.get("source_partition") == str(partition).encode()
                    and headers.get("source_offset") == str(offset).encode()):
                return {"key": msg.key(), "value": msg.value(), "headers": headers}
        return None
    finally:
        reader.close()


def test_mensaje_invalido_en_kafka_llega_a_dlq_y_se_confirma_su_offset():
    key = f"DLQ-{uuid.uuid4().hex[:8]}".encode()
    value = b'{"vehicle_id": "sin-event-id", "lat": "no-numerico"}'

    partition, offset = produce_raw(value, key)
    wait_for_consumer_caught_up()

    # El offset confirmado apunta al siguiente mensaje: este ya está procesado.
    assert committed_offset(partition) > offset

    dlq_msg = find_in_dlq(partition, offset)
    assert dlq_msg is not None, "el mensaje no apareció en la DLQ"
    assert dlq_msg["key"] == key
    assert dlq_msg["value"] == value
    assert dlq_msg["headers"]["dlq_reason"]
