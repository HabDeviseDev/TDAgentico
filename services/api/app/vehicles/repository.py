"""Consultas a vehicle_current_position."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .schemas import VehiclePosition

log = logging.getLogger("api.repository")

CURRENT_POSITIONS_SQL = """
SELECT vehicle_id, event_id::text AS event_id, captured_at, lat, lon,
       speed_kmh, updated_at
FROM vehicle_current_position
ORDER BY vehicle_id
LIMIT %(limit)s
"""


class ReadTimeout(TimeoutError):
    """La lectura (conexión + consulta) superó el tiempo permitido."""


class VehicleRepository:
    def __init__(self, pool: AsyncConnectionPool, max_rows: int, read_timeout_s: float,
                 cancel_timeout_s: float = 1.0):
        self._pool = pool
        self._max_rows = max_rows
        self._read_timeout_s = read_timeout_s
        self._cancel_timeout_s = cancel_timeout_s

    async def current_positions(self) -> list[VehiclePosition]:
        """Lectura con límite total: obtener la conexión del pool y ejecutar
        la consulta deben terminar en `read_timeout_s`."""
        try:
            return await asyncio.wait_for(self._read(), self._read_timeout_s)
        except asyncio.TimeoutError as exc:
            raise ReadTimeout(
                f"la lectura de posiciones superó {self._read_timeout_s:g} s"
            ) from exc

    async def _read(self) -> list[VehiclePosition]:
        async with self._pool.connection() as conn:
            try:
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(CURRENT_POSITIONS_SQL, {"limit": self._max_rows})
                    rows = await cur.fetchall()
            except asyncio.CancelledError:
                # Timeout (o apagado): la consulta quedó a medias.
                await self._discard(conn)
                raise
        return [VehiclePosition(**row) for row in rows]

    async def _discard(self, conn) -> None:
        """Cancela la consulta en el servidor y cierra la conexión.

        Al salir del `async with pool.connection()`, el pool recibe la conexión
        cerrada, la descarta y crea otra cuando haga falta.
        """
        try:
            await asyncio.wait_for(conn.cancel_safe(timeout=self._cancel_timeout_s),
                                   self._cancel_timeout_s)
        except Exception as exc:  # noqa: BLE001 - el servidor puede no responder
            log.warning("No se pudo cancelar la consulta en el servidor: %r", exc)
        with contextlib.suppress(Exception):
            await conn.close()
        log.warning("Conexión descartada tras cancelar una lectura de posiciones")
