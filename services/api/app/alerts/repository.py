"""Lectura de zonas e historial para evaluar alertas.

Una evaluación hace tres consultas (cuatro si hay vehículos con solo
muestras futuras) dentro de una transacción
REPEATABLE READ, READ ONLY: zonas, conteos e historial ven la misma
instantánea de la BD aunque el consumidor siga insertando.

Costo: la consulta de historial lee todas las muestras de la ventana
(vehículos × ventana / intervalo de muestreo). Con 2 h y una muestra cada
2 s son 3600 filas por vehículo. Por eso hay topes de vehículos y filas, y
la evaluación se repite cada ALERTS_EVAL_INTERVAL_S, no en cada posición.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from ..timed_read import TimedReader
from ..vehicles.schemas import utcnow
from .rules import FleetEvaluation, Sample, StopRuleParams, Zone, evaluate_fleet

ACTIVE_ZONES_SQL = """
SELECT zone_id, name, center_lat, center_lon, radius_m, is_demo
FROM critical_zones
WHERE active
ORDER BY zone_id
"""

# Todas las consultas usan el mismo instante de evaluación (`evaluated_at`)
# como límite superior: las muestras posteriores se conservan en el
# historial pero todavía no cuentan.

# Sin muestras evaluables en la ventana, pero con alguna posterior a
# `evaluated_at` (reloj del emisor adelantado).
_FUTURE_ONLY_FILTER = """
    f.captured_at > %(evaluated_at)s
    AND NOT EXISTS (SELECT 1 FROM position_history h
                    WHERE h.vehicle_id = f.vehicle_id
                      AND h.captured_at >= %(window_start)s
                      AND h.captured_at <= %(evaluated_at)s)
"""

COUNTS_SQL = f"""
SELECT
  (SELECT count(DISTINCT vehicle_id) FROM position_history
    WHERE captured_at >= %(window_start)s AND captured_at <= %(evaluated_at)s),
  (SELECT count(*) FROM vehicle_current_position
    WHERE captured_at < %(window_start)s),
  (SELECT count(DISTINCT f.vehicle_id) FROM position_history f
    WHERE {_FUTURE_ONLY_FILTER})
"""

FUTURE_ONLY_SQL = f"""
SELECT f.vehicle_id, min(f.captured_at)
FROM position_history f
WHERE {_FUTURE_ONLY_FILTER}
GROUP BY f.vehicle_id
ORDER BY f.vehicle_id
LIMIT %(max_vehicles)s
"""

# Una muestra por instante (mayor event_id), ordenada por vehículo y tiempo.
# Usa la exclusión de chunks por captured_at y el índice de la hypertable
# sobre captured_at; ver README ("Costo de la evaluación").
# Los vehículos evaluados son los primeros `max_vehicles` por vehicle_id: en
# lugar de un JOIN (que el planificador puede resolver con un bucle anidado
# que recorre la ventana una vez por vehículo) se filtra por el último id.
HISTORY_SQL = """
WITH bound AS (
    SELECT max(vehicle_id) AS last_id
    FROM (SELECT DISTINCT vehicle_id
          FROM position_history
          WHERE captured_at >= %(window_start)s
            AND captured_at <= %(evaluated_at)s
          ORDER BY vehicle_id
          LIMIT %(max_vehicles)s) v
)
SELECT DISTINCT ON (h.vehicle_id, h.captured_at)
       h.vehicle_id, h.event_id::text, h.captured_at, h.lat, h.lon, h.speed_kmh
FROM position_history h, bound
WHERE h.captured_at >= %(window_start)s
  AND h.captured_at <= %(evaluated_at)s
  AND h.vehicle_id <= bound.last_id
ORDER BY h.vehicle_id, h.captured_at, h.event_id DESC
LIMIT %(row_limit)s
"""


@dataclass(frozen=True)
class _Snapshot:
    zones: list[Zone]
    vehicles_in_window: int
    vehicles_without_recent: int
    future_only_total: int
    future_only: list[tuple[str, datetime]]
    rows: list[Sample]


class AlertRepository:
    def __init__(self, pool, params: StopRuleParams, *, max_vehicles: int, max_rows: int,
                 read_timeout_s: float, cancel_timeout_s: float = 1.0,
                 clock: Callable[[], datetime] = utcnow):
        self._reader = TimedReader(pool, read_timeout_s, cancel_timeout_s)
        self.params = params
        self.max_vehicles = max_vehicles
        self.max_rows = max_rows
        self._clock = clock

    async def active_zones(self) -> list[Zone]:
        async def work(conn):
            async with conn.cursor() as cur:
                await cur.execute(ACTIVE_ZONES_SQL)
                return [Zone(*row) for row in await cur.fetchall()]
        return await self._reader.run(work, "zonas")

    async def evaluate(self) -> FleetEvaluation:
        now = self._clock()
        window_start = now - timedelta(seconds=self.params.window_s)
        snap = await self._reader.run(lambda conn: self._read(conn, window_start, now), "alertas")
        # La regla es CPU pura: fuera del event loop para no frenar el SSE.
        return await asyncio.to_thread(
            evaluate_fleet, snap.rows, snap.zones, self.params, now, window_start,
            vehicles_in_window=snap.vehicles_in_window,
            max_vehicles=self.max_vehicles, max_rows=self.max_rows,
            vehicles_without_recent_samples=snap.vehicles_without_recent,
            future_only=snap.future_only, future_only_total=snap.future_only_total)

    async def _read(self, conn, window_start: datetime, evaluated_at: datetime) -> _Snapshot:
        params = {"window_start": window_start, "evaluated_at": evaluated_at,
                  "max_vehicles": self.max_vehicles, "row_limit": self.max_rows + 1}
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                await cur.execute(ACTIVE_ZONES_SQL)
                zones = [Zone(*row) for row in await cur.fetchall()]
                await cur.execute(COUNTS_SQL, params)
                in_window, without_recent, future_total = await cur.fetchone()
                future_only: list[tuple[str, datetime]] = []
                if future_total:
                    await cur.execute(FUTURE_ONLY_SQL, params)
                    future_only = [(v, t) for v, t in await cur.fetchall()]
                await cur.execute(HISTORY_SQL, params)
                rows = [Sample(*row) for row in await cur.fetchall()]
        return _Snapshot(zones, in_window, without_recent, future_total, future_only, rows)
