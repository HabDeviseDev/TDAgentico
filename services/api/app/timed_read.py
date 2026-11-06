"""Lecturas con límite de tiempo sobre el pool de conexiones.

El límite cubre obtener la conexión del pool y todo el trabajo con ella.
Si vence, la operación pendiente se cancela en el servidor y la conexión se
cierra: al devolverla, el pool la descarta y repone otra cuando haga falta.
Lo usan la consulta de posiciones actuales y la evaluación de alertas.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, Awaitable, Callable, TypeVar

log = logging.getLogger("api.repository")

T = TypeVar("T")


class ReadTimeout(TimeoutError):
    """La lectura (conexión + consulta) superó el tiempo permitido."""


class TimedReader:
    def __init__(self, pool: Any, read_timeout_s: float, cancel_timeout_s: float = 1.0):
        self._pool = pool
        self.read_timeout_s = read_timeout_s
        self._cancel_timeout_s = cancel_timeout_s

    async def run(self, work: Callable[[Any], Awaitable[T]], what: str) -> T:
        """Ejecuta `work(conn)` con una conexión del pool en `read_timeout_s`."""
        try:
            return await asyncio.wait_for(self._with_connection(work, what),
                                          self.read_timeout_s)
        except asyncio.TimeoutError as exc:
            raise ReadTimeout(
                f"la lectura de {what} superó {self.read_timeout_s:g} s"
            ) from exc

    async def _with_connection(self, work: Callable[[Any], Awaitable[T]], what: str) -> T:
        async with self._pool.connection() as conn:
            try:
                return await work(conn)
            except asyncio.CancelledError:
                # Timeout (o apagado): la consulta quedó a medias.
                await self._discard(conn, what)
                raise

    async def _discard(self, conn: Any, what: str) -> None:
        """Cancela la consulta en el servidor y cierra la conexión."""
        try:
            await asyncio.wait_for(conn.cancel_safe(timeout=self._cancel_timeout_s),
                                   self._cancel_timeout_s)
        except Exception as exc:  # noqa: BLE001 - el servidor puede no responder
            log.warning("No se pudo cancelar la consulta en el servidor: %r", exc)
        with contextlib.suppress(Exception):
            await conn.close()
        log.warning("Conexión descartada tras cancelar una lectura de %s", what)
