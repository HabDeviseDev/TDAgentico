"""Escritura idempotente de un lote en una sola transacción.

Por cada evento:
  a) se reclama el event_id en ingested_events (si ya existe, es duplicado
     y no se toca nada más);
  b) se inserta en el historial;
  c) se actualiza la posición visible solo si (captured_at, event_id) es
     mayor que el que ya está guardado.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import psycopg

from fleet_contracts import PositionEvent

log = logging.getLogger("consumer.persistence")

CLAIM_SQL = """
INSERT INTO ingested_events
    (event_id, vehicle_id, captured_at, payload_hash,
     kafka_topic, kafka_partition, kafka_offset)
VALUES (%(event_id)s, %(vehicle_id)s, %(captured_at)s, %(payload_hash)s,
        %(topic)s, %(partition)s, %(offset)s)
ON CONFLICT (event_id) DO NOTHING
RETURNING event_id
"""

EXISTING_HASH_SQL = "SELECT payload_hash FROM ingested_events WHERE event_id = %s"

HISTORY_SQL = """
INSERT INTO position_history
    (event_id, vehicle_id, captured_at, lat, lon, speed_kmh)
VALUES (%(event_id)s, %(vehicle_id)s, %(captured_at)s,
        %(lat)s, %(lon)s, %(speed_kmh)s)
ON CONFLICT DO NOTHING
"""

CURRENT_SQL = """
INSERT INTO vehicle_current_position AS cur
    (event_id, vehicle_id, captured_at, lat, lon, speed_kmh)
VALUES (%(event_id)s, %(vehicle_id)s, %(captured_at)s,
        %(lat)s, %(lon)s, %(speed_kmh)s)
ON CONFLICT (vehicle_id) DO UPDATE
SET event_id    = EXCLUDED.event_id,
    captured_at = EXCLUDED.captured_at,
    lat         = EXCLUDED.lat,
    lon         = EXCLUDED.lon,
    speed_kmh   = EXCLUDED.speed_kmh,
    updated_at  = now()
WHERE (cur.captured_at, cur.event_id) < (EXCLUDED.captured_at, EXCLUDED.event_id)
"""


@dataclass(frozen=True)
class InboundEvent:
    event: PositionEvent
    topic: str
    partition: int
    offset: int


@dataclass
class BatchStats:
    inserted: int = 0
    duplicates: int = 0
    id_conflicts: int = 0
    current_updated: int = 0


def persist_batch(conn: psycopg.Connection, batch: list[InboundEvent]) -> BatchStats:
    """Escribe el lote completo en una transacción. Si algo falla, no queda
    nada escrito y la excepción sube para que no se confirmen offsets."""
    stats = BatchStats()
    with conn.transaction():
        with conn.cursor() as cur:
            for item in batch:
                ev = item.event
                params = {
                    "event_id": ev.event_id,
                    "vehicle_id": ev.vehicle_id,
                    "captured_at": ev.captured_at,
                    "lat": ev.lat,
                    "lon": ev.lon,
                    "speed_kmh": ev.speed_kmh,
                    "payload_hash": ev.payload_hash(),
                    "topic": item.topic,
                    "partition": item.partition,
                    "offset": item.offset,
                }

                cur.execute(CLAIM_SQL, params)
                if cur.fetchone() is None:
                    stats.duplicates += 1
                    cur.execute(EXISTING_HASH_SQL, (ev.event_id,))
                    row = cur.fetchone()
                    if row is not None and bytes(row[0]) != params["payload_hash"]:
                        stats.id_conflicts += 1
                        log.warning(
                            "event_id=%s repetido con payload distinto; "
                            "se conserva el primero (partition=%d offset=%d)",
                            ev.event_id, item.partition, item.offset,
                        )
                    continue

                cur.execute(HISTORY_SQL, params)
                cur.execute(CURRENT_SQL, params)
                stats.inserted += 1
                stats.current_updated += cur.rowcount
    return stats
