"""Timeout de lectura con el stack real.

Una transacción de la prueba toma un LOCK exclusivo sobre
vehicle_current_position: cualquier SELECT de la API queda esperando
indefinidamente hasta que se libere.
"""

from __future__ import annotations

import os
import time

import psycopg

from conftest import API_URL, DATABASE_URL
from sse_client import SSEClient

READ_TIMEOUT_S = float(os.getenv("DB_READ_TIMEOUT_S", "3"))
POLL_INTERVAL_S = float(os.getenv("VEHICLES_POLL_INTERVAL_S", "1"))

# Solo la consulta de posiciones actuales (hub de snapshots y GET /v1/vehicles).
# La evaluación de alertas también lee vehicle_current_position y puede quedar
# bloqueada a la vez; no se cuenta aquí.
BLOCKED_API_READS_SQL = """
SELECT count(*) FROM pg_stat_activity
WHERE application_name = 'fleet-api'
  AND wait_event_type = 'Lock'
  AND query ILIKE '%%speed_kmh, updated_at%%FROM vehicle_current_position%%'
"""


def blocked_api_reads(db) -> int:
    return db.execute(BLOCKED_API_READS_SQL).fetchone()[0]


def test_lectura_bloqueada_da_503_degrada_el_sse_y_se_recupera(api, db):
    with SSEClient(API_URL) as sse:
        before = sse.next_snapshot()

        locker = psycopg.connect(DATABASE_URL)  # sin autocommit: mantiene la transacción
        try:
            locker.execute("LOCK TABLE vehicle_current_position IN ACCESS EXCLUSIVE MODE")

            # GET: responde 503 al vencer el timeout, no se queda colgado.
            start = time.monotonic()
            resp = api.get("/v1/vehicles")
            elapsed = time.monotonic() - start
            assert resp.status_code == 503
            assert resp.json()["detail"] == "La base de datos no respondió a tiempo"
            assert READ_TIMEOUT_S <= elapsed < READ_TIMEOUT_S + 3

            # SSE: el hub informa degraded.
            _, degraded = sse.wait_for(
                lambda t, d: t == "status" and d["status"] == "degraded",
                timeout=READ_TIMEOUT_S + POLL_INTERVAL_S + 10)
            assert degraded["detail"] == "La base de datos no respondió a tiempo"
            assert degraded["last_snapshot_at"] is not None

            # Varios ciclos del hub con la tabla bloqueada: las lecturas vencidas
            # se cancelan en el servidor y no se acumulan (a lo sumo la del ciclo
            # en curso queda esperando).
            time.sleep(READ_TIMEOUT_S * 2)
            assert blocked_api_reads(db) <= 1
        finally:
            locker.rollback()
            locker.close()

        # El siguiente ciclo recupera la lectura: status ok + snapshot completo.
        sse.wait_for(lambda t, d: t == "status" and d["status"] == "ok",
                     timeout=READ_TIMEOUT_S + POLL_INTERVAL_S + 10)
        after = sse.next_snapshot(timeout=5)

    assert after["sequence"] > before["sequence"]
    rest = api.get("/v1/vehicles")
    assert rest.status_code == 200  # el pool sigue sirviendo conexiones
    assert {v["vehicle_id"] for v in after["vehicles"]} == {
        v["vehicle_id"] for v in rest.json()["vehicles"]}
    assert blocked_api_reads(db) == 0
