"""Pool asíncrono de conexiones a TimescaleDB (psycopg 3).

Las consultas usan `await`, así que no bloquean el event loop.
"""

from __future__ import annotations

from psycopg_pool import AsyncConnectionPool

from .settings import Settings


def create_pool(cfg: Settings) -> AsyncConnectionPool:
    return AsyncConnectionPool(
        cfg.database_url,
        min_size=cfg.db_pool_min,
        max_size=cfg.db_pool_max,
        timeout=cfg.db_pool_timeout_s,
        # Solo lecturas: autocommit evita transacciones abiertas y cada
        # SELECT ve únicamente datos ya confirmados (READ COMMITTED).
        kwargs={"autocommit": True},
        # Descarta conexiones rotas (p. ej. tras reiniciar la BD).
        check=AsyncConnectionPool.check_connection,
        open=False,
    )
