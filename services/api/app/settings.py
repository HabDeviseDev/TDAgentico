"""Configuración de la API leída de variables de entorno."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    kafka_bootstrap: str = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9094")
    kafka_send_timeout_s: float = float(os.getenv("KAFKA_SEND_TIMEOUT_S", "5"))
    kafka_startup_retries: int = int(os.getenv("KAFKA_STARTUP_RETRIES", "30"))

    database_url: str = os.getenv("DATABASE_URL", "postgresql://fleet:fleet@localhost:5432/fleet")
    db_pool_min: int = int(os.getenv("DB_POOL_MIN", "1"))
    db_pool_max: int = int(os.getenv("DB_POOL_MAX", "5"))
    # Espera máxima por una conexión del pool antes de considerar la BD caída.
    db_pool_timeout_s: float = float(os.getenv("DB_POOL_TIMEOUT_S", "3"))
    # Límite total de una lectura de posiciones actuales: adquirir la conexión
    # + ejecutar la consulta. Aplica a GET /v1/vehicles y al hub SSE.
    db_read_timeout_s: float = float(os.getenv("DB_READ_TIMEOUT_S", "3"))

    # Cada cuánto la instancia lee vehicle_current_position para el SSE.
    vehicles_poll_interval_s: float = float(os.getenv("VEHICLES_POLL_INTERVAL_S", "1"))
    # Tope de filas por lectura (protege memoria y tamaño del snapshot).
    vehicles_max: int = int(os.getenv("VEHICLES_MAX", "10000"))
    sse_heartbeat_s: float = float(os.getenv("SSE_HEARTBEAT_S", "15"))
    sse_max_clients: int = int(os.getenv("SSE_MAX_CLIENTS", "500"))


settings = Settings()
