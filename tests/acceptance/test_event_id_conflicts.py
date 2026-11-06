"""Conflictos de event_id: mismo ID con contenido distinto.

- Prueba controlada (necesita solo TimescaleDB): llama a persist_batch como
  lo haría el consumidor y simula el reproceso del mismo mensaje de Kafka.
- Prueba de stack: el conflicto entra por la API y lo registra el consumidor.
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timezone

import psycopg

from conftest import DATABASE_URL, make_event, wait_for_consumer_caught_up
from consumer.persistence import InboundEvent, persist_batch
from fleet_contracts import PositionEvent

T0 = datetime(2026, 11, 5, 9, 0, tzinfo=timezone.utc)


def conflicts_for(db, event_id) -> list[tuple]:
    return db.execute(
        "SELECT original_hash, received_hash, kafka_topic, kafka_partition, "
        "kafka_offset, detected_at FROM event_id_conflicts WHERE event_id = %s",
        (event_id,),
    ).fetchall()


def test_reproceso_de_mensaje_conflictivo_conserva_original_y_registra_una_vez(db, vehicle_id):
    topic = "test.controlled"  # tópico ficticio: no choca con offsets reales
    offset = random.randint(10**9, 10**12)
    original = PositionEvent(event_id=uuid.uuid4(), vehicle_id=vehicle_id,
                             captured_at=T0, lat=-33.0, lon=-70.0, speed_kmh=10.0)
    tampered = original.model_copy(update={"speed_kmh": 99.0})
    assert tampered.payload_hash() != original.payload_hash()

    with psycopg.connect(DATABASE_URL) as conn:
        first = persist_batch(conn, [InboundEvent(original, topic, 0, offset)])
        conflict_msg = InboundEvent(tampered, topic, 0, offset + 1)
        received = persist_batch(conn, [conflict_msg])
        # Reproceso del mismo mensaje (mismo tópico/partición/offset), p. ej.
        # tras una caída antes del commit de offset.
        reprocessed = persist_batch(conn, [conflict_msg])

    assert (first.inserted, received.id_conflicts, reprocessed.id_conflicts) == (1, 1, 0)

    # El original se conserva en el historial, la deduplicación y el estado actual.
    speeds = db.execute("SELECT speed_kmh FROM position_history WHERE event_id = %s",
                        (original.event_id,)).fetchall()
    assert speeds == [(10.0,)]
    stored_hash = db.execute("SELECT payload_hash FROM ingested_events WHERE event_id = %s",
                             (original.event_id,)).fetchone()[0]
    assert bytes(stored_hash) == original.payload_hash()
    assert db.execute("SELECT speed_kmh FROM vehicle_current_position WHERE vehicle_id = %s",
                      (vehicle_id,)).fetchone()[0] == 10.0

    # Un único registro del conflicto, con todos sus datos.
    rows = conflicts_for(db, original.event_id)
    assert len(rows) == 1
    orig_hash, recv_hash, r_topic, r_partition, r_offset, detected_at = rows[0]
    assert bytes(orig_hash) == original.payload_hash()
    assert bytes(recv_hash) == tampered.payload_hash()
    assert (r_topic, r_partition, r_offset) == (topic, 0, offset + 1)
    assert detected_at is not None


def test_reenvio_identico_no_es_conflicto(db, vehicle_id):
    topic, offset = "test.controlled", random.randint(10**9, 10**12)
    event = PositionEvent(event_id=uuid.uuid4(), vehicle_id=vehicle_id,
                          captured_at=T0, lat=1.0, lon=2.0, speed_kmh=3.0)
    with psycopg.connect(DATABASE_URL) as conn:
        persist_batch(conn, [InboundEvent(event, topic, 0, offset)])
        stats = persist_batch(conn, [InboundEvent(event, topic, 0, offset + 1)])
    assert (stats.duplicates, stats.id_conflicts) == (1, 0)
    assert conflicts_for(db, event.event_id) == []


def test_conflicto_por_la_api_queda_registrado(api, db, vehicle_id):
    original = make_event(vehicle_id, T0, speed_kmh=10.0)
    tampered = {**original, "speed_kmh": 99.0}

    assert api.post("/v1/positions", json=original).status_code == 202
    assert api.post("/v1/positions", json=tampered).status_code == 202
    wait_for_consumer_caught_up()

    rows = conflicts_for(db, original["event_id"])
    assert len(rows) == 1
    assert rows[0][2] == "fleet.positions.v1"
    speeds = db.execute("SELECT speed_kmh FROM position_history WHERE vehicle_id = %s",
                        (vehicle_id,)).fetchall()
    assert speeds == [(10.0,)]
