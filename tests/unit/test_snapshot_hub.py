"""Pruebas controladas del hub de snapshots y del stream SSE (BD simulada)."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import main as api_main
from app.vehicles import routes as vehicle_routes
from app.vehicles.schemas import VehiclePosition
from app.vehicles.snapshots import SnapshotHub
from app.vehicles.sse import client_stream, format_event

T0 = datetime(2026, 11, 6, 10, 0, tzinfo=timezone.utc)


def position(vehicle_id: str, event_id: str = "e1", speed: float = 30.0,
             captured_at: datetime = T0) -> VehiclePosition:
    return VehiclePosition(vehicle_id=vehicle_id, event_id=event_id, captured_at=captured_at,
                           lat=4.65, lon=-74.08, speed_kmh=speed, updated_at=captured_at)


class FakeDb:
    def __init__(self, rows=None):
        self.rows: list[VehiclePosition] = rows or []
        self.fail = False
        self.calls = 0

    async def fetch(self) -> list[VehiclePosition]:
        self.calls += 1
        if self.fail:
            raise ConnectionError("BD caída")
        return list(self.rows)


def run(coro):
    return asyncio.run(coro)


def decode(events):
    return [(e.type, json.loads(e.data)) for e in events]


def test_snapshot_inicial_al_suscribirse():
    async def scenario():
        db = FakeDb([position("V1")])
        hub = SnapshotHub(db.fetch, interval_s=1)
        await hub.poll_once()
        sub = hub.subscribe()
        return decode(await sub.next(timeout=0.1))

    events = run(scenario())
    assert [t for t, _ in events] == ["status", "snapshot"]
    assert events[0][1]["status"] == "ok"
    snapshot = events[1][1]
    assert snapshot["type"] == "snapshot"
    assert snapshot["count"] == 1
    assert snapshot["vehicles"][0]["vehicle_id"] == "V1"
    assert snapshot["vehicles"][0]["captured_at"] == "2026-11-06T10:00:00.000000Z"
    assert snapshot["generated_at"].endswith("Z")


def test_solo_emite_snapshot_cuando_cambia_el_estado_visible():
    async def scenario():
        db = FakeDb([position("V1")])
        hub = SnapshotHub(db.fetch, interval_s=1)
        await hub.poll_once()
        sub = hub.subscribe()
        await sub.next(timeout=0.1)  # status + snapshot inicial

        await hub.poll_once()  # sin cambios
        unchanged = await sub.next(timeout=0.05)

        db.rows = [position("V1", event_id="e2", speed=0.0)]
        await hub.poll_once()
        changed = decode(await sub.next(timeout=0.1))
        return unchanged, changed, db.calls

    unchanged, changed, calls = run(scenario())
    assert unchanged is None  # None = solo correspondería un heartbeat
    assert [t for t, _ in changed] == ["snapshot"]
    assert changed[0][1]["sequence"] == 2
    assert changed[0][1]["vehicles"][0]["event_id"] == "e2"
    assert calls == 3  # una lectura por ciclo, sin importar los clientes


def test_una_lectura_compartida_para_varios_clientes():
    async def scenario():
        db = FakeDb([position("V1")])
        hub = SnapshotHub(db.fetch, interval_s=1)
        subs = [hub.subscribe() for _ in range(25)]
        await hub.poll_once()
        received = [await s.next(timeout=0.1) for s in subs]
        return db.calls, received

    calls, received = run(scenario())
    assert calls == 1
    # Todos reciben el mismo evento ya serializado.
    snapshots = {next(e for e in evs if e.type == "snapshot") for evs in received}
    assert len(snapshots) == 1


def test_error_de_bd_se_comunica_y_se_recupera():
    async def scenario():
        db = FakeDb([position("V1")])
        hub = SnapshotHub(db.fetch, interval_s=1)
        await hub.poll_once()
        sub = hub.subscribe()
        await sub.next(timeout=0.1)

        db.fail = True
        await hub.poll_once()
        degraded = decode(await sub.next(timeout=0.1))
        await hub.poll_once()  # sigue caída: no repite el estado
        repeated = await sub.next(timeout=0.05)

        # Un cliente que conecta durante la caída ve el estado y el último snapshot.
        late = hub.subscribe()
        late_events = decode(await late.next(timeout=0.1))

        db.fail = False
        db.rows = [position("V1", event_id="e9")]
        await hub.poll_once()
        recovered = decode(await sub.next(timeout=0.1))
        return degraded, repeated, late_events, recovered

    degraded, repeated, late_events, recovered = run(scenario())
    assert degraded == [("status", degraded[0][1])]
    assert degraded[0][1]["status"] == "degraded"
    assert degraded[0][1]["detail"]
    assert degraded[0][1]["last_snapshot_at"] is not None
    assert repeated is None
    assert [t for t, _ in late_events] == ["status", "snapshot"]
    assert late_events[0][1]["status"] == "degraded"
    assert [t for t, _ in recovered] == ["status", "snapshot"]
    assert recovered[0][1]["status"] == "ok"
    assert recovered[1][1]["vehicles"][0]["event_id"] == "e9"


def test_recuperacion_emite_snapshot_aunque_el_estado_no_cambie():
    async def scenario():
        db = FakeDb([position("V1")])
        hub = SnapshotHub(db.fetch, interval_s=1)
        await hub.poll_once()
        sub = hub.subscribe()
        await sub.next(timeout=0.1)
        db.fail = True
        await hub.poll_once()
        await sub.next(timeout=0.1)
        db.fail = False
        await hub.poll_once()
        return decode(await sub.next(timeout=0.1))

    events = run(scenario())
    assert [t for t, _ in events] == ["status", "snapshot"]


def test_cliente_lento_no_acumula_memoria():
    async def scenario():
        db = FakeDb()
        hub = SnapshotHub(db.fetch, interval_s=1)
        slow = hub.subscribe()
        for i in range(1000):
            db.rows = [position("V1", event_id=f"e{i}")]
            await hub.poll_once()
        pending = slow.pending_count
        events = decode(await slow.next(timeout=0.1))
        return pending, events

    pending, events = run(scenario())
    assert pending <= 2  # como máximo un status y un snapshot
    snapshot = dict(events)["snapshot"]
    assert snapshot["vehicles"][0]["event_id"] == "e999"
    assert snapshot["sequence"] == 1000


def test_stream_envia_heartbeat_y_libera_la_suscripcion():
    async def scenario():
        hub = SnapshotHub(FakeDb().fetch, interval_s=1)
        stream = client_stream(hub, heartbeat_s=0.05)
        first = await stream.__anext__()
        during = hub.subscriber_count
        heartbeat = await stream.__anext__()
        await stream.aclose()  # lo que hace Starlette al desconectarse el cliente
        return first, during, heartbeat, hub.subscriber_count

    first, during, heartbeat, after = run(scenario())
    assert first.startswith("retry:")
    assert during == 1
    assert heartbeat.startswith("event: heartbeat\n")
    assert '"generated_at"' in heartbeat
    assert after == 0


def test_stream_cancelado_libera_la_suscripcion():
    async def scenario():
        hub = SnapshotHub(FakeDb().fetch, interval_s=1)

        async def consume():
            async for _ in client_stream(hub, heartbeat_s=10):
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        during = hub.subscriber_count
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return during, hub.subscriber_count

    assert run(scenario()) == (1, 0)


def test_formato_sse():
    from app.vehicles.snapshots import ServerEvent

    text = format_event(ServerEvent("snapshot", '{"a":1}', id="7"))
    assert text == 'event: snapshot\nid: 7\ndata: {"a":1}\n\n'


# --- GET /v1/vehicles con repositorio simulado --------------------------------
class FakeRepo:
    def __init__(self, rows=None, fail=False):
        self.rows, self.fail = rows or [], fail

    async def current_positions(self):
        if self.fail:
            raise ConnectionError("BD caída")
        return self.rows


@pytest.fixture
def client_with_repo():
    def make(repo):
        api_main.app.dependency_overrides[vehicle_routes.get_repository] = lambda: repo
        return TestClient(api_main.app)

    yield make
    api_main.app.dependency_overrides.clear()


def test_get_vehicles_devuelve_utc_y_fecha_de_generacion(client_with_repo):
    client = client_with_repo(FakeRepo([position("V1", speed=12.5)]))
    resp = client.get("/v1/vehicles")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["generated_at"].endswith("Z")
    v = body["vehicles"][0]
    assert set(v) == {"vehicle_id", "event_id", "captured_at", "lat", "lon",
                      "speed_kmh", "updated_at"}
    assert v["captured_at"] == "2026-11-06T10:00:00.000000Z"


def test_get_vehicles_503_si_falla_la_bd(client_with_repo):
    client = client_with_repo(FakeRepo(fail=True))
    assert client.get("/v1/vehicles").status_code == 503
