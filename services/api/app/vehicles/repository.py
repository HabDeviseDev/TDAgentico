"""Consultas a vehicle_current_position."""

from __future__ import annotations

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .schemas import VehiclePosition

CURRENT_POSITIONS_SQL = """
SELECT vehicle_id, event_id::text AS event_id, captured_at, lat, lon,
       speed_kmh, updated_at
FROM vehicle_current_position
ORDER BY vehicle_id
LIMIT %(limit)s
"""


class VehicleRepository:
    def __init__(self, pool: AsyncConnectionPool, max_rows: int):
        self._pool = pool
        self._max_rows = max_rows

    async def current_positions(self) -> list[VehiclePosition]:
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(CURRENT_POSITIONS_SQL, {"limit": self._max_rows})
                rows = await cur.fetchall()
        return [VehiclePosition(**row) for row in rows]
