"""POST /v1/positions/batch con el stack real (API → Kafka → consumidor → TimescaleDB)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from conftest import current_position, dedup_count, history_rows, make_event, wait_for_consumer_caught_up


def test_lote_con_atrasado_invalido_y_reenvio_sin_duplicados(api, db, vehicle_id):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    newest = make_event(vehicle_id, now - timedelta(seconds=10), speed_kmh=30.0)
    late = make_event(vehicle_id, now - timedelta(minutes=5), speed_kmh=20.0)   # llega después
    invalid = make_event(vehicle_id, now, speed_kmh=-1)
    batch = {"events": [newest, late, invalid, "basura"]}

    resp = api.post("/v1/positions/batch", json=batch)
    assert resp.status_code == 200
    body = resp.json()
    assert [r["status"] for r in body["results"]] == ["accepted", "accepted", "rejected", "rejected"]
    assert [r["index"] for r in body["results"]] == [0, 1, 2, 3]
    assert body["results"][2]["event_id"] == invalid["event_id"]
    assert body["results"][3]["event_id"] is None

    wait_for_consumer_caught_up()
    assert len(history_rows(db, vehicle_id)) == 2           # el atrasado también se guarda
    current = current_position(db, vehicle_id)
    assert str(current[0]) == newest["event_id"]            # la posición visible es la más reciente

    # La respuesta "se perdió": el móvil reenvía exactamente el mismo lote.
    again = api.post("/v1/positions/batch", json=batch).json()
    assert [r["status"] for r in again["results"]][:2] == ["accepted", "accepted"]
    wait_for_consumer_caught_up()
    assert len(history_rows(db, vehicle_id)) == 2           # sin duplicados
    assert dedup_count(db, newest["event_id"]) == 1
    assert dedup_count(db, late["event_id"]) == 1


def test_limites_del_lote_en_el_stack(api):
    too_many = {"events": [make_event(f"TEST-{uuid.uuid4().hex[:8]}", datetime.now(timezone.utc))
                           for _ in range(201)]}
    resp = api.post("/v1/positions/batch", json=too_many)
    assert resp.status_code == 413
    assert api.post("/v1/positions/batch", json={"events": []}).status_code == 422
