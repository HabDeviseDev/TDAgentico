"""Timeout de lectura de posiciones con un pool simulado que se cuelga.

Usa el VehicleRepository real sobre un pool falso: la consulta (o la
adquisición de la conexión) espera indefinidamente.
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app import main as api_main
from app.vehicles import routes as vehicle_routes
from app.vehicles.repository import ReadTimeout, VehicleRepository
from app.vehicles.snapshots import SnapshotHub

TIMEOUT_S = 0.2
T0 = datetime(2026, 11, 6, 10, 0, tzinfo=timezone.utc)
ROW = {"vehicle_id": "V1", "event_id": "e1", "captured_at": T0, "lat": 4.65,
       "lon": -74.08, "speed_kmh": 30.0, "updated_at": T0}


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params):
        self.conn.executed += 1
        if self.conn.mode == "hang":
            await asyncio.Event().wait()  # nunca se completa

    async def fetchall(self):
        return [dict(r) for r in self.conn.rows]


class FakeConn:
    def __init__(self, mode: str, rows):
        self.mode, self.rows = mode, rows
        self.executed = 0
        self.cancel_requested = False
        self.closed = False

    def cursor(self, row_factory=None):
        return FakeCursor(self)

    async def cancel_safe(self, timeout: float = 30.0):
        self.cancel_requested = True

    async def close(self):
        self.closed = True


class FakePool:
    """mode: 'ok' | 'hang' (consulta colgada) | 'hang_acquire' (sin conexión)."""

    def __init__(self, mode: str = "ok", rows=None):
        self.mode = mode
        self.rows = rows if rows is not None else [ROW]
        self.handed_out: list[FakeConn] = []
        self.returned: list[FakeConn] = []

    @asynccontextmanager
    async def connection(self):
        if self.mode == "hang_acquire":
            await asyncio.Event().wait()
        conn = FakeConn(self.mode, self.rows)
        self.handed_out.append(conn)
        try:
            yield conn
        finally:
            # Como psycopg_pool: si vuelve cerrada, se descarta.
            self.returned.append(conn)

    @property
    def discarded(self) -> list[FakeConn]:
        return [c for c in self.returned if c.closed]


def repo_for(pool: FakePool) -> VehicleRepository:
    return VehicleRepository(pool, max_rows=100, read_timeout_s=TIMEOUT_S, cancel_timeout_s=0.1)


def run(coro):
    return asyncio.run(coro)


def timed(coro_fn):
    async def wrapper():
        start = time.monotonic()
        try:
            await coro_fn()
        except ReadTimeout as exc:
            return exc, time.monotonic() - start
        return None, time.monotonic() - start
    return wrapper


def test_consulta_colgada_supera_el_timeout_se_cancela_y_descarta_la_conexion():
    pool = FakePool("hang")
    exc, elapsed = run(timed(repo_for(pool).current_positions)())

    assert isinstance(exc, ReadTimeout)
    assert TIMEOUT_S <= elapsed < TIMEOUT_S + 0.5
    conn = pool.handed_out[0]
    assert conn.cancel_requested            # se pidió cancelar en el servidor
    assert conn.closed                      # no se reutiliza
    assert pool.returned == [conn]          # se devolvió al pool (liberada)
    assert pool.discarded == [conn]


def test_adquisicion_de_conexion_colgada_tambien_respeta_el_timeout():
    pool = FakePool("hang_acquire")
    exc, elapsed = run(timed(repo_for(pool).current_positions)())
    assert isinstance(exc, ReadTimeout)
    assert elapsed < TIMEOUT_S + 0.5
    assert pool.handed_out == []


def test_lectura_normal_no_descarta_conexiones():
    pool = FakePool("ok")
    rows = run(repo_for(pool).current_positions())
    assert [v.vehicle_id for v in rows] == ["V1"]
    assert pool.discarded == []


def test_hub_con_lectura_colgada_degrada_conserva_snapshot_y_se_recupera():
    async def scenario():
        pool = FakePool("ok")
        hub = SnapshotHub(repo_for(pool).current_positions, interval_s=0.05)
        await hub.poll_once()
        sub = hub.subscribe()
        initial = [(e.type, json.loads(e.data)) for e in await sub.next(timeout=0.1)]

        # La BD deja de responder: la consulta se queda esperando.
        pool.mode = "hang"
        start = time.monotonic()
        await hub.poll_once()
        elapsed = time.monotonic() - start
        degraded = [(e.type, json.loads(e.data)) for e in await sub.next(timeout=0.1)]

        # Un cliente que conecta ahora recibe el estado y el último snapshot.
        late = [(e.type, json.loads(e.data)) for e in await hub.subscribe().next(timeout=0.1)]

        # Siguiente ciclo: sigue colgada → no repite el estado, nueva conexión.
        await hub.poll_once()
        repeated = await sub.next(timeout=0.05)

        # La BD responde de nuevo con datos nuevos.
        pool.mode = "ok"
        pool.rows = [dict(ROW, event_id="e2")]
        await hub.poll_once()
        recovered = [(e.type, json.loads(e.data)) for e in await sub.next(timeout=0.1)]
        return initial, elapsed, degraded, late, repeated, recovered, pool

    initial, elapsed, degraded, late, repeated, recovered, pool = run(scenario())

    assert [t for t, _ in initial] == ["status", "snapshot"]
    assert elapsed < TIMEOUT_S + 0.5
    assert [t for t, _ in degraded] == ["status"]
    assert degraded[0][1]["status"] == "degraded"
    assert degraded[0][1]["detail"] == "La base de datos no respondió a tiempo"
    assert dict(late)["snapshot"]["vehicles"][0]["event_id"] == "e1"  # último snapshot
    assert repeated is None
    assert [t for t, _ in recovered] == ["status", "snapshot"]
    assert recovered[0][1]["status"] == "ok"
    assert recovered[1][1]["vehicles"][0]["event_id"] == "e2"
    assert recovered[1][1]["count"] == 1
    # Las dos lecturas colgadas se cancelaron y sus conexiones se descartaron.
    assert len(pool.discarded) == 2


def test_get_vehicles_responde_503_cuando_la_lectura_se_cuelga():
    pool = FakePool("hang")
    api_main.app.dependency_overrides[vehicle_routes.get_repository] = lambda: repo_for(pool)
    try:
        start = time.monotonic()
        resp = TestClient(api_main.app).get("/v1/vehicles")
        elapsed = time.monotonic() - start
    finally:
        api_main.app.dependency_overrides.clear()

    assert resp.status_code == 503
    assert resp.json()["detail"] == "La base de datos no respondió a tiempo"
    assert elapsed < TIMEOUT_S + 1
    assert pool.discarded == pool.handed_out != []
