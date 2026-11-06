"""Hub de alertas, repositorio y rutas con una BD simulada y reloj controlado."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import main as api_main
from app.alerts import routes as alert_routes
from app.alerts.hub import AlertHub
from app.alerts.repository import AlertRepository
from app.alerts.rules import Sample, StopRuleParams, Zone
from app.vehicles.schemas import VehiclePosition
from app.vehicles.snapshots import SnapshotHub
from app.vehicles.sse import client_stream

T0 = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
ZONE = ("Z1", "Demo · Zona 1", 4.6230, -74.1150, 400.0, True)
P = StopRuleParams()
TIMEOUT_S = 0.2


class Clock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def stopped(vehicle: str, start: datetime, end: datetime, speed: float = 0.0,
            step_s: int = 60) -> list[Sample]:
    out, t, n = [], start, 0
    while t <= end:
        n += 1
        out.append(Sample(vehicle, f"00000000-0000-0000-0000-{abs(hash((vehicle, t))) % 10**12:012d}",
                          t, ZONE[2], ZONE[3], speed))
        t += timedelta(seconds=step_s)
    return out


class FakeDb:
    """Responde a las consultas del AlertRepository a partir de listas en memoria."""

    def __init__(self, rows: list[Sample], zones=(ZONE,)):
        self.rows = rows
        self.zones = list(zones)
        self.mode = "ok"            # ok | hang | fail
        self.connections: list[FakeConn] = []
        self.returned: list[FakeConn] = []

    @asynccontextmanager
    async def connection(self):
        conn = FakeConn(self)
        self.connections.append(conn)
        try:
            yield conn
        finally:
            self.returned.append(conn)

    @property
    def discarded(self):
        return [c for c in self.returned if c.closed]


class FakeConn:
    def __init__(self, db: FakeDb):
        self.db = db
        self.closed = False
        self.cancel_requested = False
        self.statements: list[str] = []

    @asynccontextmanager
    async def transaction(self):
        self.statements.append("BEGIN")
        yield
        self.statements.append("COMMIT")

    def cursor(self):
        return FakeCursor(self)

    async def cancel_safe(self, timeout: float = 30.0):
        self.cancel_requested = True

    async def close(self):
        self.closed = True


class FakeCursor:
    def __init__(self, conn: FakeConn):
        self.conn = conn
        self.result: list[tuple] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql: str, params=None):
        db = self.conn.db
        self.conn.statements.append(" ".join(sql.split()))
        if "position_history h" in sql:
            if db.mode == "hang":
                await asyncio.Event().wait()
            if db.mode == "fail":
                raise ConnectionError("BD caída")
            ws = params["window_start"]
            rows = sorted((r for r in db.rows if r.captured_at >= ws),
                          key=lambda r: (r.vehicle_id, r.captured_at))
            vehicles = sorted({r.vehicle_id for r in rows})[:params["max_vehicles"]]
            rows = [r for r in rows if r.vehicle_id in vehicles][:params["row_limit"]]
            self.result = [(r.vehicle_id, r.event_id, r.captured_at, r.lat, r.lon, r.speed_kmh)
                           for r in rows]
        elif "count(DISTINCT" in sql:
            ws = params["window_start"]
            self.result = [(len({r.vehicle_id for r in db.rows if r.captured_at >= ws}), 0)]
        elif "critical_zones" in sql:
            self.result = list(db.zones)
        else:
            self.result = []

    async def fetchall(self):
        return list(self.result)

    async def fetchone(self):
        return self.result[0]


def make(db: FakeDb, clock: Clock, max_vehicles=100, max_rows=10_000):
    repo = AlertRepository(db, P, max_vehicles=max_vehicles, max_rows=max_rows,
                           read_timeout_s=TIMEOUT_S, cancel_timeout_s=0.1, clock=clock)
    hub = AlertHub(repo.evaluate, interval_s=0.05, max_vehicles=max_vehicles,
                   max_rows=max_rows, clock=clock)
    return repo, hub


def decode(events):
    return [(e.type, json.loads(e.data)) for e in events or []]


def run(coro):
    return asyncio.run(coro)


# -- repositorio --------------------------------------------------------------

def test_evaluacion_en_una_transaccion_repeatable_read_de_solo_lectura():
    clock = Clock(T0)
    db = FakeDb(stopped("V1", T0 - timedelta(minutes=25), T0))
    repo, _ = make(db, clock)
    ev = run(repo.evaluate())
    assert [a.vehicle_id for a in ev.alerts] == ["V1"]
    stmts = db.connections[0].statements
    assert stmts[0] == "BEGIN" and stmts[-1] == "COMMIT"
    assert stmts[1] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
    # Zonas, conteos e historial: tres consultas en total, no una por vehículo.
    assert len(stmts) == 2 + 1 + 3


def test_consulta_colgada_supera_el_timeout_y_descarta_la_conexion():
    db = FakeDb(stopped("V1", T0 - timedelta(minutes=25), T0))
    db.mode = "hang"
    repo, _ = make(db, Clock(T0))
    start = time.monotonic()
    try:
        run(repo.evaluate())
        raise AssertionError("debía vencer el timeout")
    except TimeoutError:
        pass
    assert time.monotonic() - start < TIMEOUT_S + 0.5
    [conn] = db.connections
    assert conn.cancel_requested and conn.closed
    assert db.discarded == [conn]


# -- hub ----------------------------------------------------------------------

def test_al_conectar_recibe_estado_y_resultado_completo():
    async def scenario():
        clock = Clock(T0)
        _, hub = make(FakeDb(stopped("V1", T0 - timedelta(minutes=25), T0)), clock)
        await hub.poll_once()
        return decode(await hub.subscribe().next(timeout=0.1))

    events = run(scenario())
    assert [t for t, _ in events] == ["alerts_status", "alerts"]
    status, alerts = events[0][1], events[1][1]
    assert status["status"] == "ok"
    assert alerts["complete"] is True
    assert alerts["alert_count"] == 1
    assert alerts["alerts"][0]["observed_duration_s"] == 25 * 60
    assert alerts["parameters"]["alert_after_s"] == 1200
    assert alerts["evaluated_at"].endswith("Z")


def test_detecta_cambios_aunque_la_posicion_actual_no_cambie():
    async def scenario():
        clock = Clock(T0)
        db = FakeDb(stopped("V1", T0 - timedelta(minutes=30), T0))
        _, hub = make(db, clock)
        await hub.poll_once()
        sub = hub.subscribe()
        await sub.next(timeout=0.1)

        # Nada cambia: no se difunde otro resultado.
        clock.now += timedelta(seconds=30)
        await hub.poll_once()
        unchanged = await sub.next(timeout=0.05)

        # Evento atrasado de movimiento (la última muestra sigue igual).
        db.rows.append(Sample("V1", "ffffffff-0000-0000-0000-000000000001",
                              T0 - timedelta(minutes=12, seconds=30), ZONE[2], ZONE[3], 30.0))
        await hub.poll_once()
        after_late = decode(await sub.next(timeout=0.1))

        # Pasa el tiempo: la última muestra supera la antigüedad máxima.
        clock.now = T0 + timedelta(seconds=121)
        await hub.poll_once()
        after_stale = decode(await sub.next(timeout=0.1))
        return unchanged, after_late, after_stale

    unchanged, after_late, after_stale = run(scenario())
    assert unchanged is None
    assert [t for t, _ in after_late] == ["alerts"]
    assert after_late[0][1]["alerts"] == []
    assert after_late[0][1]["complete"] is True
    stale = after_stale[0][1]
    assert stale["alerts"] == []
    assert stale["insufficient_evidence_count"] == 1
    assert stale["insufficient_evidence"][0]["reason"] == "stale_last_sample"


def test_timeout_degrada_conserva_el_resultado_y_se_recupera():
    async def scenario():
        clock = Clock(T0)
        db = FakeDb(stopped("V1", T0 - timedelta(minutes=25), T0))
        _, hub = make(db, clock)
        await hub.poll_once()
        sub = hub.subscribe()
        initial = decode(await sub.next(timeout=0.1))

        db.mode = "hang"
        clock.now += timedelta(seconds=5)
        start = time.monotonic()
        await hub.poll_once()
        elapsed = time.monotonic() - start
        degraded = decode(await sub.next(timeout=0.1))
        late_joiner = decode(await hub.subscribe().next(timeout=0.1))

        await hub.poll_once()                       # sigue colgada
        repeated = await sub.next(timeout=0.05)

        db.mode = "ok"
        await hub.poll_once()
        recovered = decode(await sub.next(timeout=0.1))
        return initial, elapsed, degraded, late_joiner, repeated, recovered, db

    initial, elapsed, degraded, late_joiner, repeated, recovered, db = run(scenario())
    assert elapsed < TIMEOUT_S + 0.5
    assert [t for t, _ in degraded] == ["alerts_status"]
    status = degraded[0][1]
    assert status["status"] == "degraded"
    assert status["detail"] == "La base de datos no respondió a tiempo"
    assert status["last_evaluation_at"] == initial[1][1]["evaluated_at"]
    # Quien conecta durante el fallo recibe el estado y el último resultado
    # (con su hora de evaluación), no una lista vacía.
    assert [t for t, _ in late_joiner] == ["alerts_status", "alerts"]
    assert late_joiner[1][1]["alert_count"] == 1
    assert repeated is None
    # Recuperación: ok + resultado completo aunque no haya cambiado.
    assert [t for t, _ in recovered] == ["alerts_status", "alerts"]
    assert recovered[0][1]["status"] == "ok"
    assert recovered[1][1]["alert_count"] == 1
    assert len(db.discarded) == 2


def test_sin_resultado_previo_un_fallo_no_publica_lista_vacia():
    async def scenario():
        db = FakeDb([])
        db.mode = "fail"
        _, hub = make(db, Clock(T0))
        await hub.poll_once()
        return decode(await hub.subscribe().next(timeout=0.1))

    events = run(scenario())
    assert [t for t, _ in events] == ["alerts_status"]
    assert events[0][1]["status"] == "degraded"
    assert events[0][1]["detail"] == "No se pudo evaluar las alertas"
    assert events[0][1]["last_evaluation_at"] is None


def test_limites_se_informan_como_evaluacion_parcial():
    async def scenario():
        clock = Clock(T0)
        rows = (stopped("A", T0 - timedelta(minutes=25), T0)
                + stopped("B", T0 - timedelta(minutes=25), T0)
                + stopped("C", T0 - timedelta(minutes=25), T0))
        _, hub = make(FakeDb(rows), clock, max_vehicles=2)
        await hub.poll_once()
        return decode(await hub.subscribe().next(timeout=0.1))[1][1]

    result = run(scenario())
    assert result["complete"] is False
    assert result["incomplete_reasons"] == ["max_vehicles"]
    assert result["coverage"]["vehicles_in_window"] == 3
    assert result["coverage"]["vehicles_not_evaluated"] == 1
    assert [a["vehicle_id"] for a in result["alerts"]] == ["A", "B"]


# -- stream compartido ----------------------------------------------------------

def test_stream_entrega_posiciones_y_alertas_y_libera_ambas_suscripciones():
    async def scenario():
        clock = Clock(T0)
        _, alert_hub = make(FakeDb(stopped("V1", T0 - timedelta(minutes=25), T0)), clock)

        async def fetch():
            return [VehiclePosition(vehicle_id="V1", event_id="e1", captured_at=T0,
                                    lat=4.623, lon=-74.115, speed_kmh=0, updated_at=T0)]
        snap_hub = SnapshotHub(fetch, interval_s=1)
        await snap_hub.poll_once()
        await alert_hub.poll_once()

        stream = client_stream(snap_hub, 0.05, alert_hub)
        chunks = [await stream.__anext__() for _ in range(5)]
        counts_open = (snap_hub.subscriber_count, alert_hub.subscriber_count)
        await stream.aclose()
        return chunks, counts_open, (snap_hub.subscriber_count, alert_hub.subscriber_count)

    chunks, counts_open, counts_closed = run(scenario())
    types = [c.split("\n")[0] for c in chunks[1:]]
    assert types == ["event: status", "event: snapshot", "event: alerts_status", "event: alerts"]
    assert counts_open == (1, 1)
    assert counts_closed == (0, 0)


def test_cliente_lento_guarda_un_evento_por_tipo():
    async def scenario():
        clock = Clock(T0)
        db = FakeDb(stopped("V1", T0 - timedelta(minutes=25), T0))
        _, hub = make(db, clock)
        sub = hub.subscribe()
        for i in range(30):                 # 30 resultados distintos sin leer
            db.rows.append(Sample("V1", f"ffffffff-0000-0000-0000-{i:012d}",
                                  T0 + timedelta(seconds=i + 1), ZONE[2], ZONE[3], 0.0))
            clock.now = T0 + timedelta(seconds=i + 1)
            await hub.poll_once()
        pending = sub.pending_count
        events = decode(await sub.next(timeout=0.1))
        return pending, events

    pending, events = run(scenario())
    assert pending == 2                     # alerts_status + alerts
    alerts = dict(events)["alerts"]
    assert alerts["alerts"][0]["observed_duration_s"] == 25 * 60 + 30


# -- rutas ----------------------------------------------------------------------

def _client_with(repo):
    api_main.app.dependency_overrides[alert_routes.get_alert_repository] = lambda: repo
    return TestClient(api_main.app)


def test_get_alerts_y_zonas():
    db = FakeDb(stopped("V1", T0 - timedelta(minutes=25), T0))
    repo, _ = make(db, Clock(T0))
    try:
        client = _client_with(repo)
        alerts = client.get("/v1/alerts")
        zones = client.get("/v1/zones")
    finally:
        api_main.app.dependency_overrides.clear()
    assert alerts.status_code == 200
    body = alerts.json()
    assert set(body) >= {"evaluated_at", "parameters", "complete", "alerts",
                         "insufficient_evidence", "coverage"}
    alert = body["alerts"][0]
    assert set(alert) >= {"alert_id", "vehicle_id", "zone_id", "started_at", "last_sample_at",
                          "observed_duration_s", "start_unknown"}
    assert zones.status_code == 200
    assert zones.json()["zones"][0]["is_demo"] is True


def test_get_alerts_responde_503_si_la_lectura_se_cuelga():
    db = FakeDb([])
    db.mode = "hang"
    repo, _ = make(db, Clock(T0))
    try:
        start = time.monotonic()
        resp = _client_with(repo).get("/v1/alerts")
        elapsed = time.monotonic() - start
    finally:
        api_main.app.dependency_overrides.clear()
    assert resp.status_code == 503
    assert resp.json()["detail"] == "La base de datos no respondió a tiempo"
    assert elapsed < TIMEOUT_S + 1
    assert db.discarded == db.connections
