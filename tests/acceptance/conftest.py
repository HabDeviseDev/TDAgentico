"""Utilidades para las pruebas de aceptación contra el stack de Docker Compose.

Requiere `docker compose up -d --build` antes de ejecutar pytest.
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import httpx
import psycopg
import pytest
from confluent_kafka import Consumer, TopicPartition

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "contracts"))

from fleet_contracts import TOPIC  # noqa: E402

API_URL = os.getenv("API_URL", "http://localhost:8000")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://fleet:fleet@localhost:5432/fleet")
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9094")
GROUP_ID = os.getenv("KAFKA_GROUP_ID", "fleet-ingestion")
WAIT_TIMEOUT_S = float(os.getenv("WAIT_TIMEOUT_S", "30"))


def make_event(vehicle_id: str, captured_at: datetime, *, event_id: uuid.UUID | None = None,
               lat: float = -33.44889, lon: float = -70.669265, speed_kmh: float = 42.5) -> dict:
    return {
        "schema_version": 1,
        "event_id": str(event_id or uuid.uuid4()),
        "vehicle_id": vehicle_id,
        "captured_at": captured_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "lat": lat,
        "lon": lon,
        "speed_kmh": speed_kmh,
    }


def consumer_lag() -> int:
    """Mensajes del tópico aún no confirmados por el grupo del consumidor."""
    probe = Consumer({
        "bootstrap.servers": KAFKA_BOOTSTRAP,
        "group.id": GROUP_ID,
        "enable.auto.commit": False,
    })
    try:
        md = probe.list_topics(TOPIC, timeout=5)
        parts = [TopicPartition(TOPIC, p) for p in md.topics[TOPIC].partitions]
        lag = 0
        for tp in probe.committed(parts, timeout=5):
            low, high = probe.get_watermark_offsets(TopicPartition(TOPIC, tp.partition), timeout=5)
            committed = tp.offset if tp.offset >= 0 else low
            lag += high - committed
        return lag
    finally:
        probe.close()


def wait_until(predicate: Callable[[], bool], timeout: float = WAIT_TIMEOUT_S,
               message: str = "condición") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.25)
    raise AssertionError(f"Tiempo agotado esperando: {message}")


def wait_for_consumer_caught_up(timeout: float = WAIT_TIMEOUT_S) -> None:
    wait_until(lambda: consumer_lag() == 0, timeout, "lag del consumidor = 0")


@pytest.fixture(scope="session")
def api() -> httpx.Client:
    with httpx.Client(base_url=API_URL, timeout=10) as client:
        try:
            client.get("/health").raise_for_status()
        except httpx.HTTPError as exc:
            pytest.exit(f"La API no responde en {API_URL}: {exc}. ¿Corriste docker compose up?")
        yield client


@pytest.fixture(scope="session")
def db() -> psycopg.Connection:
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        yield conn


@pytest.fixture
def vehicle_id() -> str:
    # Un vehículo nuevo por prueba: las pruebas no dependen del estado previo.
    return f"TEST-{uuid.uuid4().hex[:12]}"


def history_rows(db: psycopg.Connection, vehicle_id: str) -> list[tuple]:
    return db.execute(
        "SELECT event_id, captured_at, lat, lon, speed_kmh FROM position_history "
        "WHERE vehicle_id = %s ORDER BY captured_at, event_id",
        (vehicle_id,),
    ).fetchall()


def dedup_count(db: psycopg.Connection, event_id: str) -> int:
    return db.execute(
        "SELECT count(*) FROM ingested_events WHERE event_id = %s", (event_id,)
    ).fetchone()[0]


def current_position(db: psycopg.Connection, vehicle_id: str) -> tuple | None:
    return db.execute(
        "SELECT event_id, captured_at, lat, lon, speed_kmh, updated_at "
        "FROM vehicle_current_position WHERE vehicle_id = %s",
        (vehicle_id,),
    ).fetchone()
