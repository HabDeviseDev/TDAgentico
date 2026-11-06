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

    # Regla de detención en zonas críticas (valores iniciales de demostración).
    alert_max_speed_kmh: float = float(os.getenv("ALERT_MAX_SPEED_KMH", "3"))
    alert_max_displacement_m: float = float(os.getenv("ALERT_MAX_DISPLACEMENT_M", "50"))
    alert_max_gap_s: float = float(os.getenv("ALERT_MAX_GAP_S", "120"))
    alert_max_sample_age_s: float = float(os.getenv("ALERT_MAX_SAMPLE_AGE_S", "120"))
    # Alerta cuando la duración observada es MAYOR que este valor.
    alert_after_s: float = float(os.getenv("ALERT_AFTER_S", "1200"))
    # Ventana de historial que se evalúa.
    alert_window_s: float = float(os.getenv("ALERT_WINDOW_S", "7200"))
    # Topes por evaluación; si se alcanzan, el resultado se marca parcial.
    alerts_max_vehicles: int = int(os.getenv("ALERTS_MAX_VEHICLES", "1000"))
    alerts_max_rows: int = int(os.getenv("ALERTS_MAX_ROWS", "100000"))
    # Cada cuánto la instancia reevalúa las alertas para el SSE.
    alerts_eval_interval_s: float = float(os.getenv("ALERTS_EVAL_INTERVAL_S", "5"))

    def stop_rule_params(self):
        from .alerts.rules import StopRuleParams
        return StopRuleParams(
            max_speed_kmh=self.alert_max_speed_kmh,
            max_displacement_m=self.alert_max_displacement_m,
            max_gap_s=self.alert_max_gap_s,
            max_sample_age_s=self.alert_max_sample_age_s,
            alert_after_s=self.alert_after_s,
            window_s=self.alert_window_s,
        )


settings = Settings()
