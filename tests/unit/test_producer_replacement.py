"""Reemplazo del productor Kafka tras un error fatal (sin el stack).

Reproduce lo que vio el escenario de caos C: tras reiniciar Kafka, el productor
idempotente de aiokafka queda con OutOfOrderSequenceNumber y falla en cada envío.
ResilientProducer debe crear uno nuevo (uno solo, aunque fallen muchos envíos a
la vez) sin cambiar la respuesta: el envío que falló sigue siendo 503/retryable
y el reenvío con el mismo event_id se confirma con el productor nuevo.

Todo corre en un único event loop (asyncio.run + httpx.ASGITransport), porque
el reemplazo es una tarea en segundo plano. ASGITransport no ejecuta el
lifespan: la API nunca conecta a Kafka.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import httpx
import pytest
from aiokafka.errors import (
    KafkaConnectionError,
    KafkaError,
    OutOfOrderSequenceNumber,
    ProducerFenced,
    UnknownProducerId,
)

from app import ingest
from app import main as api_main


class FakeProducer:
    """Doble de AIOKafkaProducer: falla con `error` las primeras `fail_times`
    veces (None = siempre) y luego confirma."""

    def __init__(self, name: str, error: BaseException | None = None, fail_times: int | None = None):
        self.name, self.error, self.fail_times = name, error, fail_times
        self.calls = 0
        self.acked: list[bytes] = []
        self.stopped = False

    async def send_and_wait(self, topic, value=None, key=None, headers=None):
        self.calls += 1
        await asyncio.sleep(0)
        if self.stopped:
            raise KafkaError("productor cerrado")
        if self.error is not None and (self.fail_times is None or self.calls <= self.fail_times):
            raise self.error
        self.acked.append(value)
        return SimpleNamespace(partition=0, offset=len(self.acked))

    async def stop(self):
        self.stopped = True


class Factory:
    """Entrega productores nuevos en orden; `fail_first` intentos fallan (Kafka caído)."""

    def __init__(self, *producers: FakeProducer, fail_first: int = 0):
        self.queue = list(producers)
        self.fail_first = fail_first
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        await asyncio.sleep(0.01)
        if self.calls <= self.fail_first:
            raise KafkaConnectionError("Kafka aún no responde")
        return self.queue.pop(0)


def resilient(initial, factory, **kwargs) -> ingest.ResilientProducer:
    kwargs.setdefault("min_interval_s", 0)
    kwargs.setdefault("stop_timeout_s", 1)
    return ingest.ResilientProducer(factory, initial, **kwargs)


async def settle(rp: ingest.ResilientProducer) -> None:
    task = rp._task
    if task is not None:
        await asyncio.wait_for(task, timeout=5)


@pytest.mark.parametrize("error", [OutOfOrderSequenceNumber(), UnknownProducerId(), ProducerFenced()])
def test_error_fatal_reemplaza_el_productor_y_el_siguiente_envio_confirma(error):
    async def run():
        broken = FakeProducer("viejo", error=error)
        fresh = FakeProducer("nuevo")
        factory = Factory(fresh)
        rp = resilient(broken, factory)

        with pytest.raises(type(error)):
            await rp.send_and_wait("t", value=b"e1", key=b"v")
        await settle(rp)

        assert rp.current is fresh and rp.replacements == 1
        assert broken.stopped and not fresh.stopped
        meta = await rp.send_and_wait("t", value=b"e1", key=b"v")
        assert meta.offset == 1 and fresh.acked == [b"e1"]

    asyncio.run(run())


def test_muchos_fallos_simultaneos_crean_un_solo_productor():
    async def run():
        broken = FakeProducer("viejo", error=OutOfOrderSequenceNumber())
        factory = Factory(FakeProducer("nuevo"), FakeProducer("sobra"))
        rp = resilient(broken, factory)

        results = await asyncio.gather(*(rp.send_and_wait("t", value=b"x", key=b"v")
                                         for _ in range(20)), return_exceptions=True)
        assert all(isinstance(r, OutOfOrderSequenceNumber) for r in results)
        await settle(rp)
        # Un envío tardío con el productor viejo (ya reemplazado) no dispara otro reemplazo.
        rp._schedule_replacement(0, OutOfOrderSequenceNumber())
        await settle(rp)

        assert factory.calls == 1 and rp.replacements == 1
        assert rp.current.name == "nuevo"

    asyncio.run(run())


def test_si_kafka_aun_no_responde_conserva_el_anterior_y_reintenta_con_el_siguiente_error():
    async def run():
        broken = FakeProducer("viejo", error=OutOfOrderSequenceNumber())
        fresh = FakeProducer("nuevo")
        factory = Factory(fresh, fail_first=1)
        rp = resilient(broken, factory)

        with pytest.raises(OutOfOrderSequenceNumber):
            await rp.send_and_wait("t", value=b"e1", key=b"v")
        await settle(rp)
        assert rp.current is broken and rp.replacements == 0 and not broken.stopped

        with pytest.raises(OutOfOrderSequenceNumber):
            await rp.send_and_wait("t", value=b"e1", key=b"v")
        await settle(rp)
        assert rp.current is fresh and rp.replacements == 1 and broken.stopped
        assert factory.calls == 2

    asyncio.run(run())


def test_los_intentos_de_reemplazo_respetan_el_intervalo_minimo():
    async def run():
        broken = FakeProducer("viejo", error=OutOfOrderSequenceNumber())
        factory = Factory(FakeProducer("nuevo"), fail_first=1)
        rp = resilient(broken, factory, min_interval_s=0.3)
        loop = asyncio.get_running_loop()

        with pytest.raises(OutOfOrderSequenceNumber):
            await rp.send_and_wait("t", value=b"x", key=b"v")
        await settle(rp)
        t0 = loop.time()
        with pytest.raises(OutOfOrderSequenceNumber):
            await rp.send_and_wait("t", value=b"x", key=b"v")
        await settle(rp)
        assert loop.time() - t0 >= 0.25
        assert rp.replacements == 1

    asyncio.run(run())


def test_error_transitorio_no_reemplaza_el_productor():
    async def run():
        producer = FakeProducer("único", error=KafkaConnectionError("broker no disponible"))
        factory = Factory(FakeProducer("no debería usarse"))
        rp = resilient(producer, factory)
        with pytest.raises(KafkaConnectionError):
            await rp.send_and_wait("t", value=b"x", key=b"v")
        await settle(rp)
        assert factory.calls == 0 and rp.current is producer

    asyncio.run(run())


def test_tarea_de_envio_terminada_tambien_cuenta_como_productor_inservible():
    async def run():
        loop = asyncio.get_running_loop()
        dead = loop.create_future()
        dead.set_result(None)
        broken = FakeProducer("viejo", error=KafkaError("Unexpected error during batch delivery"))
        broken._sender = SimpleNamespace(sender_task=dead)
        factory = Factory(FakeProducer("nuevo"))
        rp = resilient(broken, factory)
        with pytest.raises(KafkaError):
            await rp.send_and_wait("t", value=b"x", key=b"v")
        await settle(rp)
        assert rp.replacements == 1

    asyncio.run(run())


def test_si_el_anterior_no_cierra_a_tiempo_se_fuerza_el_cierre():
    """Visto en el escenario C: `stop()` espera lotes que nunca se confirman."""
    class Part:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    class HangingStop(FakeProducer):
        async def stop(self):
            await asyncio.sleep(30)

    async def run():
        broken = HangingStop("viejo", error=OutOfOrderSequenceNumber())
        broken._sender, broken.client = Part(), Part()
        rp = resilient(broken, Factory(FakeProducer("nuevo")), stop_timeout_s=0.1)
        with pytest.raises(OutOfOrderSequenceNumber):
            await rp.send_and_wait("t", value=b"x", key=b"v")
        await settle(rp)
        assert rp.replacements == 1 and rp.current.name == "nuevo"
        assert broken._sender.closed and broken.client.closed

    asyncio.run(run())


def test_stop_cierra_el_productor_actual_y_no_reemplaza_mas():
    async def run():
        broken = FakeProducer("viejo", error=OutOfOrderSequenceNumber())
        factory = Factory(FakeProducer("nuevo"))
        rp = resilient(broken, factory)
        await rp.stop()
        with pytest.raises(KafkaError):
            await rp.send_and_wait("t", value=b"x", key=b"v")
        assert rp._task is None and factory.calls == 0 and broken.stopped

    asyncio.run(run())


# -- a través de la API -------------------------------------------------------------

def event() -> dict:
    return {"schema_version": 1, "event_id": str(uuid.uuid4()), "vehicle_id": "CHAOS-1",
            "captured_at": "2026-11-07T10:00:00Z", "lat": 4.65, "lon": -74.08, "speed_kmh": 10.0}


@pytest.fixture
def api_with():
    def make(rp):
        api_main.app.dependency_overrides[api_main.get_producer] = lambda: rp
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=api_main.app),
                                 base_url="http://api")

    yield make
    api_main.app.dependency_overrides.clear()


def test_api_503_y_el_reenvio_con_el_mismo_event_id_recibe_202(api_with):
    async def run():
        broken = FakeProducer("viejo", error=OutOfOrderSequenceNumber())
        fresh = FakeProducer("nuevo")
        rp = resilient(broken, Factory(fresh))
        ev = event()
        async with api_with(rp) as client:
            first = await client.post("/v1/positions", json=ev)
            assert first.status_code == 503
            assert "mismo event_id" in first.json()["detail"]
            await settle(rp)
            again = await client.post("/v1/positions", json=ev)
            assert again.status_code == 202 and again.json()["event_id"] == ev["event_id"]
        assert len(fresh.acked) == 1 and rp.replacements == 1

    asyncio.run(run())


def test_lote_queda_retryable_y_el_reenvio_se_acepta(api_with):
    async def run():
        broken = FakeProducer("viejo", error=OutOfOrderSequenceNumber())
        fresh = FakeProducer("nuevo")
        factory = Factory(fresh)
        rp = resilient(broken, factory)
        events = [event() for _ in range(5)]
        async with api_with(rp) as client:
            first = (await client.post("/v1/positions/batch", json={"events": events})).json()
            assert first["retryable"] == 5 and first["accepted"] == 0
            assert [r["event_id"] for r in first["results"]] == [e["event_id"] for e in events]
            await settle(rp)
            again = (await client.post("/v1/positions/batch", json={"events": events})).json()
            assert again["accepted"] == 5 and again["retryable"] == 0
        assert factory.calls == 1 and len(fresh.acked) == 5

    asyncio.run(run())
