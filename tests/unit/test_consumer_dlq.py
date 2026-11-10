"""Pruebas controladas del procesamiento de lotes: Kafka y la BD son dobles.

Verifican el orden DLQ confirmada → COMMIT en BD → commit de offsets, y que
un fallo de la DLQ deja el offset sin confirmar.
"""

from __future__ import annotations

import json
import uuid

import pytest
from confluent_kafka import KafkaException

from consumer.main import process_batch
from consumer.persistence import BatchStats

TOPIC = "fleet.positions.v1"


class FakeMessage:
    def __init__(self, value: bytes | None, partition: int, offset: int, key: bytes = b"V1"):
        self._value, self._partition, self._offset, self._key = value, partition, offset, key

    def error(self):
        return None

    def value(self):
        return self._value

    def key(self):
        return self._key

    def topic(self):
        return TOPIC

    def partition(self):
        return self._partition

    def offset(self):
        return self._offset

    def headers(self):
        return [("producer", b"test")]


class FakeConsumer:
    def __init__(self, timeline: list):
        self.timeline = timeline
        self.commits: list[list[tuple[str, int, int]]] = []

    def commit(self, offsets, asynchronous=True):
        assert asynchronous is False, "el commit de offsets debe ser síncrono"
        self.commits.append([(tp.topic, tp.partition, tp.offset) for tp in offsets])
        self.timeline.append("offset_commit")


class FakeDlqProducer:
    """behavior: 'ack' | 'delivery_error' | 'timeout'."""

    def __init__(self, timeline: list, behavior: str = "ack"):
        self.timeline = timeline
        self.behavior = behavior
        self.produced: list[dict] = []
        self._pending = []

    def produce(self, topic, key=None, value=None, headers=None, on_delivery=None):
        self.produced.append({"topic": topic, "key": key, "value": value,
                              "headers": dict(headers or [])})
        self._pending.append(on_delivery)
        self.timeline.append("dlq_produce")

    def flush(self, timeout=None):
        pending, self._pending = self._pending, []
        if self.behavior == "timeout":
            return len(pending)  # mensajes sin confirmar
        for cb in pending:
            if self.behavior == "delivery_error":
                cb("_MSG_TIMED_OUT: broker no disponible", None)
            else:
                cb(None, object())
                self.timeline.append("dlq_ack")
        return 0


def fake_persist(timeline: list):
    calls = []

    def persist(conn, batch):
        calls.append(batch)
        timeline.append("db_commit")
        return conn, BatchStats(inserted=len(batch))

    persist.calls = calls
    return persist


def valid_value() -> bytes:
    return json.dumps({
        "schema_version": 1, "event_id": str(uuid.uuid4()), "vehicle_id": "V1",
        "captured_at": "2026-11-05T10:00:00Z", "lat": 1.0, "lon": 2.0, "speed_kmh": 3.0,
    }).encode()


INVALID = b'{"esto": "no es una posicion"}'


def test_mensaje_invalido_va_a_dlq_y_su_offset_se_confirma_despues_del_ack():
    timeline: list = []
    consumer, dlq = FakeConsumer(timeline), FakeDlqProducer(timeline)
    persist = fake_persist(timeline)

    process_batch([FakeMessage(INVALID, partition=1, offset=7)],
                  consumer=consumer, dlq_producer=dlq, conn=None, persist=persist)

    assert timeline == ["dlq_produce", "dlq_ack", "offset_commit"]
    assert consumer.commits == [[(TOPIC, 1, 8)]]
    assert persist.calls == []

    sent = dlq.produced[0]
    assert sent["topic"] == "fleet.positions.dlq"
    assert sent["value"] == INVALID
    assert sent["headers"]["source_partition"] == b"1"
    assert sent["headers"]["source_offset"] == b"7"
    assert sent["headers"]["dlq_reason"]


def test_lote_mixto_confirma_offsets_despues_de_dlq_y_bd():
    timeline: list = []
    consumer, dlq = FakeConsumer(timeline), FakeDlqProducer(timeline)
    persist = fake_persist(timeline)
    messages = [
        FakeMessage(valid_value(), partition=0, offset=10),
        FakeMessage(INVALID, partition=0, offset=11),
        FakeMessage(valid_value(), partition=2, offset=5),
    ]

    process_batch(messages, consumer=consumer, dlq_producer=dlq, conn=None, persist=persist)

    assert timeline == ["dlq_produce", "dlq_ack", "db_commit", "offset_commit"]
    assert sorted(consumer.commits[0]) == [(TOPIC, 0, 12), (TOPIC, 2, 6)]
    assert len(persist.calls[0]) == 2


@pytest.mark.parametrize("behavior", ["delivery_error", "timeout"])
def test_si_falla_la_dlq_el_offset_queda_sin_confirmar(behavior):
    timeline: list = []
    consumer, dlq = FakeConsumer(timeline), FakeDlqProducer(timeline, behavior)
    persist = fake_persist(timeline)
    messages = [
        FakeMessage(valid_value(), partition=0, offset=20),
        FakeMessage(INVALID, partition=0, offset=21),
    ]

    with pytest.raises(KafkaException):
        process_batch(messages, consumer=consumer, dlq_producer=dlq, conn=None, persist=persist)

    assert consumer.commits == []
    assert "offset_commit" not in timeline
    # Tampoco se escribe el lote: al reiniciar se relee completo.
    assert persist.calls == []


def test_si_falla_la_bd_no_se_confirman_offsets():
    timeline: list = []
    consumer, dlq = FakeConsumer(timeline), FakeDlqProducer(timeline)

    def failing_persist(conn, batch):
        raise RuntimeError("fallo en la transacción")

    with pytest.raises(RuntimeError):
        process_batch([FakeMessage(valid_value(), partition=0, offset=1)],
                      consumer=consumer, dlq_producer=dlq, conn=None, persist=failing_persist)

    assert consumer.commits == []
