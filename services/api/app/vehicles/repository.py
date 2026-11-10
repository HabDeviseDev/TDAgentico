"""Consultas a vehicle_current_position."""

from __future__ import annotations

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from ..timed_read import ReadTimeout, TimedReader
from .schemas import VehiclePosition

__all__ = ["ReadTimeout", "VehicleRepository", "CURRENT_POSITIONS_SQL"]

CURRENT_POSITIONS_SQL = """
SELECT vehicle_id, event_id::text AS event_id, captured_at, lat, lon,
       speed_kmh, updated_at
FROM vehicle_current_position
ORDER BY vehicle_id
LIMIT %(limit)s
"""


class VehicleRepository:
    def __init__(self, pool: AsyncConnectionPool, max_rows: int, read_timeout_s: float,
                 cancel_timeout_s: float = 1.0):
        self._reader = TimedReader(pool, read_timeout_s, cancel_timeout_s)
        self._max_rows = max_rows

    async def current_positions(self) -> list[VehiclePosition]:
        """Lectura con límite total: obtener la conexión del pool y ejecutar
        la consulta deben terminar en `read_timeout_s`."""
        return await self._reader.run(self._read, "posiciones")

    async def _read(self, conn) -> list[VehiclePosition]:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(CURRENT_POSITIONS_SQL, {"limit": self._max_rows})
            rows = await cur.fetchall()
        return [VehiclePosition(**row) for row in rows]
