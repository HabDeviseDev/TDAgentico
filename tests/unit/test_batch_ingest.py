"""POST /v1/positions/batch con un productor Kafka doble (sin el stack).

TestClient sin `with`: no se ejecuta el lifespan, la API no conecta a Kafka.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace

import pytest
from aiokafka.errors import KafkaConnectionError
from fastapi.testclient import TestClient

from app import ingest
from app import main as api_main


def event(vehicle_id: str = "MOB-1", **overrides) -> dict:
    base = {"schema_version": 1, "event_id": str(uuid.uuid4()), "vehicle_id": vehicle_id,
            "captured_at": "2026-11-06T10:00:00.000Z", "lat": 4.65, "lon": -74.08, "speed_kmh": 12.5}
    base.update(overrides)
    return base


class FakeProducer:
    """Confirma salvo para vehículos en `fail` (error) o `hang` (sin ack)."""

    def __init__(self, fail=(), hang=()):
        self.fail, self.hang = set(fail), set(hang)
        self.sent: list[tuple[bytes, bytes]] = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def send_and_wait(self, topic, value=None, key=None, headers=None):
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0.01)
            vehicle = key.decode()
            if vehicle in self.fail:
                raise KafkaConnectionError("broker no disponible")
            if vehicle in self.hang:
                await asyncio.sleep(30)
            self.sent.append((key, value))
            return SimpleNamespace(partition=0, offset=len(self.sent))
        finally:
            self.in_flight -= 1


@pytest.fixture
def client(monkeypatch):
    holder = {}

    def make(**kwargs):
        fake = FakeProducer(**kwargs)
        api_main.app.dependency_overrides[api_main.get_producer] = lambda: fake
        holder["fake"] = fake
        return TestClient(api_main.app), fake

    monkeypatch.setattr(ingest, "SEND_TIMEOUT_S", 0.3)
    yield make
    api_main.app.dependency_overrides.clear()


def post(client, events):
    return client.post("/v1/positions/batch", json={"events": events})


def test_resultado_por_evento_con_indice_e_id_tambien_si_es_invalido(client):
    c, fake = client()
    good = event()
    bad_speed = event(speed_kmh=-3)
    no_id = event()
    del no_id["event_id"]
    future = event(captured_at="2099-01-01T00:00:00Z")
    naive = event(captured_at="2026-11-06T10:00:00")
    resp = post(c, [good, bad_speed, "no soy un objeto", no_id, future, naive])
    assert resp.status_code == 200
    body = resp.json()
    assert body["received"] == 6 and body["accepted"] == 1 and body["rejected"] == 5
    results = body["results"]
    assert [r["index"] for r in results] == [0, 1, 2, 3, 4, 5]
    assert results[0] == {"index": 0, "event_id": good["event_id"], "status": "accepted", "error": None}
    assert results[1]["status"] == "rejected" and results[1]["event_id"] == bad_speed["event_id"]
    assert "speed_kmh" in results[1]["error"]
    assert results[2]["event_id"] is None and "objeto" in results[2]["error"]
    assert results[3]["event_id"] is None and "event_id" in results[3]["error"]
    assert results[4]["event_id"] == future["event_id"] and "futuro" in results[4]["error"]
    assert "captured_at" in results[5]["error"]
    assert len(fake.sent) == 1                        # solo se publicó el válido


def test_lote_parcialmente_publicado(client):
    c, fake = client(fail={"FALLA"}, hang={"COLGADO"})
    events = [event("A"), event("FALLA"), event("B"), event("COLGADO")]
    body = post(c, events).json()
    assert [r["status"] for r in body["results"]] == ["accepted", "retryable", "accepted", "retryable"]
    assert [r["event_id"] for r in body["results"]] == [e["event_id"] for e in events]
    assert "Kafka no confirmó" in body["results"][1]["error"]
    assert body["accepted"] == 2 and body["retryable"] == 2
    assert fake.max_in_flight == 4                    # se publican en paralelo, no en serie


def test_reenvio_del_mismo_lote_conserva_identidad_y_contenido(client):
    c, fake = client()
    events = [event("A"), event("B")]
    first = post(c, events).json()
    second = post(c, events).json()       # p. ej. la respuesta anterior se perdió
    assert [r["event_id"] for r in first["results"]] == [r["event_id"] for r in second["results"]]
    assert fake.sent[:2] == fake.sent[2:]  # mismos bytes publicados: el consumidor deduplica


def test_misma_validacion_y_publicacion_que_el_endpoint_individual(client):
    c, fake = client()
    e = event("IGUAL")
    assert c.post("/v1/positions", json=e).status_code == 202
    assert post(c, [e]).json()["results"][0]["status"] == "accepted"
    assert fake.sent[0] == fake.sent[1]    # misma clave y mismo JSON canónico
    invalid = event(lat=123)
    assert c.post("/v1/positions", json=invalid).status_code == 422
    assert post(c, [invalid]).json()["results"][0]["status"] == "rejected"


def test_endpoint_individual_conserva_202_y_503(client):
    c, _ = client(fail={"FALLA"})
    ok = c.post("/v1/positions", json=event("OK"))
    assert ok.status_code == 202 and ok.json()["status"] == "accepted"
    assert c.post("/v1/positions", json=event("FALLA")).status_code == 503


def test_limite_de_eventos(client, monkeypatch):
    monkeypatch.setattr(ingest, "BATCH_MAX_EVENTS", 3)
    c, fake = client()
    assert post(c, [event() for _ in range(3)]).status_code == 200
    resp = post(c, [event() for _ in range(4)])
    assert resp.status_code == 413 and "máximo 3" in resp.json()["detail"]
    assert len(fake.sent) == 3                        # el lote excedido no publicó nada


def test_limite_de_bytes(client, monkeypatch):
    monkeypatch.setattr(ingest, "BATCH_MAX_BYTES", 1000)
    c, fake = client()
    big = [event(vehicle_id="X" * 60) for _ in range(10)]
    resp = post(c, big)
    assert resp.status_code == 413 and fake.sent == []
    # También sin Content-Length (cuerpo en trozos).
    def chunks():
        payload = json.dumps({"events": big}).encode()
        for i in range(0, len(payload), 200):
            yield payload[i:i + 200]
    resp = c.post("/v1/positions/batch", content=chunks(), headers={"Content-Type": "application/json"})
    assert resp.status_code == 413 and fake.sent == []


@pytest.mark.parametrize("payload,code", [
    (b"no es json", 400),
    (b"[]", 422),
    (b'{"events": {}}', 422),
    (b'{"events": []}', 422),
])
def test_forma_del_cuerpo(client, payload, code):
    c, fake = client()
    resp = c.post("/v1/positions/batch", content=payload, headers={"Content-Type": "application/json"})
    assert resp.status_code == code and fake.sent == []
