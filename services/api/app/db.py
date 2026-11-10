"""Pool asíncrono de conexiones a TimescaleDB (psycopg 3).

Las consultas usan `await`, así que no bloquean el event loop.
"""

from __future__ import annotations

from psycopg_pool import AsyncConnectionPool

from .settings import Settings

APPLICATION_NAME = "fleet-api"
# Margen del statement_timeout del servidor sobre el timeout de lectura del
# cliente: el cliente cancela primero; el servidor es solo la red de seguridad.
SERVER_TIMEOUT_MARGIN_S = 2


def create_pool(cfg: Settings) -> AsyncConnectionPool:
    statement_timeout_ms = int((cfg.db_read_timeout_s + SERVER_TIMEOUT_MARGIN_S) * 1000)
    return AsyncConnectionPool(
        cfg.database_url,
        min_size=cfg.db_pool_min,
        max_size=cfg.db_pool_max,
        timeout=cfg.db_pool_timeout_s,
        kwargs={
            # Solo lecturas: autocommit evita transacciones abiertas y cada
            # SELECT ve únicamente datos ya confirmados (READ COMMITTED).
            "autocommit": True,
            "application_name": APPLICATION_NAME,
            "options": f"-c statement_timeout={statement_timeout_ms}",
        },
        # Descarta conexiones rotas (p. ej. tras reiniciar la BD).
        check=AsyncConnectionPool.check_connection,
        open=False,
    )
