"""Pruebas controladas de la API: el productor Kafka se sustituye por un doble.

No necesitan el stack: TestClient sin `with` no ejecuta el lifespan, así que
la API nunca intenta conectarse a Kafka.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from aiokafka.errors import KafkaConnectionError, NotEnoughReplicasError
from fastapi.testclient import TestClient

from app import main as api_main


def valid_event() -> dict:
    return {
        "schema_version": 1,
        "event_id": str(uuid.uuid4()),
        "vehicle_id": "UNIT-1",
        "captured_at": "2026-11-05T10:00:00Z",
        "lat": -33.4,
        "lon": -70.6,
        "speed_kmh": 40.0,
    }


class FakeProducer:
    def __init__(self, behavior: str):
        self.behavior = behavior
        self.calls = 0

    async def send_and_wait(self, topic, value=None, key=None, headers=None):
        self.calls += 1
        if self.behavior == "connection_error":
            raise KafkaConnectionError("broker no disponible")
        if self.behavior == "not_enough_replicas":
            raise NotEnoughReplicasError()
        if self.behavior == "no_ack":
            await asyncio.sleep(30)  # el broker nunca confirma
        return SimpleNamespace(partition=0, offset=42)


@pytest.fixture
def client_with():
    def make(behavior: str):
        fake = FakeProducer(behavior)
        api_main.app.dependency_overrides[api_main.get_producer] = lambda: fake
        return TestClient(api_main.app), fake

    yield make
    api_main.app.dependency_overrides.clear()


def test_202_cuando_kafka_confirma(client_with):
    client, fake = client_with("ack")
    event = valid_event()
    resp = client.post("/v1/positions", json=event)
    assert resp.status_code == 202
    assert resp.json()["event_id"] == event["event_id"]
    assert fake.calls == 1


@pytest.mark.parametrize("behavior", ["connection_error", "not_enough_replicas"])
def test_503_cuando_kafka_rechaza_el_envio(client_with, behavior):
    client, fake = client_with(behavior)
    resp = client.post("/v1/positions", json=valid_event())
    assert resp.status_code == 503
    assert "mismo event_id" in resp.json()["detail"]
    assert fake.calls == 1


def test_503_cuando_kafka_no_confirma_a_tiempo(client_with, monkeypatch):
    monkeypatch.setattr(api_main, "SEND_TIMEOUT_S", 0.2)
    client, fake = client_with("no_ack")
    resp = client.post("/v1/positions", json=valid_event())
    assert resp.status_code == 503
    assert fake.calls == 1


def test_evento_invalido_no_llega_a_kafka(client_with):
    client, fake = client_with("ack")
    resp = client.post("/v1/positions", json={**valid_event(), "lat": 200})
    assert resp.status_code == 422
    assert fake.calls == 0
