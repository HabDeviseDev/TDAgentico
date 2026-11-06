"""Pruebas de aceptación del flujo API → Kafka → consumidor → TimescaleDB."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from conftest import (
    current_position,
    dedup_count,
    history_rows,
    make_event,
    wait_for_consumer_caught_up,
)

T0 = datetime(2026, 11, 5, 10, 0, 0, tzinfo=timezone.utc)


def post(api, event: dict) -> None:
    resp = api.post("/v1/positions", json=event)
    assert resp.status_code == 202, resp.text
    assert resp.json()["event_id"] == event["event_id"]


# AT-1 -----------------------------------------------------------------------
def test_at1_ingesta_normal(api, db, vehicle_id):
    e1 = make_event(vehicle_id, T0, lat=-33.5, lon=-70.6, speed_kmh=55.0)

    post(api, e1)
    wait_for_consumer_caught_up()

    rows = history_rows(db, vehicle_id)
    assert len(rows) == 1
    event_id, captured_at, lat, lon, speed = rows[0]
    assert str(event_id) == e1["event_id"]
    assert captured_at == T0  # mismo instante en UTC
    assert (lat, lon, speed) == (-33.5, -70.6, 55.0)

    assert dedup_count(db, e1["event_id"]) == 1

    current = current_position(db, vehicle_id)
    assert current is not None
    assert str(current[0]) == e1["event_id"]


# AT-2 -----------------------------------------------------------------------
def test_at2_reenvio_mismo_evento(api, db, vehicle_id):
    e1 = make_event(vehicle_id, T0)

    post(api, e1)
    wait_for_consumer_caught_up()
    before = current_position(db, vehicle_id)

    post(api, e1)
    post(api, e1)
    wait_for_consumer_caught_up()

    assert len(history_rows(db, vehicle_id)) == 1
    assert dedup_count(db, e1["event_id"]) == 1
    assert current_position(db, vehicle_id) == before  # updated_at incluido


def test_at2_mismo_id_con_payload_distinto_conserva_el_primero(api, db, vehicle_id):
    e1 = make_event(vehicle_id, T0, speed_kmh=10.0)
    tampered = {**e1, "speed_kmh": 99.0}

    post(api, e1)
    post(api, tampered)
    wait_for_consumer_caught_up()

    rows = history_rows(db, vehicle_id)
    assert len(rows) == 1
    assert rows[0][4] == 10.0


# AT-3 -----------------------------------------------------------------------
def test_at3_posicion_antigua_despues_de_reciente(api, db, vehicle_id):
    recent = make_event(vehicle_id, T0 + timedelta(minutes=5), lat=-33.40)
    old = make_event(vehicle_id, T0, lat=-33.50)

    post(api, recent)
    post(api, old)
    wait_for_consumer_caught_up()

    rows = history_rows(db, vehicle_id)
    assert [str(r[0]) for r in rows] == [old["event_id"], recent["event_id"]]

    current = current_position(db, vehicle_id)
    assert str(current[0]) == recent["event_id"]
    assert current[2] == -33.40


@pytest.mark.parametrize("reverse", [False, True], ids=["menor-primero", "mayor-primero"])
def test_at3_empate_de_captured_at_gana_event_id_mayor(api, db, vehicle_id, reverse):
    ids = sorted([uuid.uuid4(), uuid.uuid4()])  # mismo orden que el tipo uuid de Postgres
    tie = T0 + timedelta(minutes=10)
    events = [make_event(vehicle_id, tie, event_id=i) for i in ids]
    if reverse:
        events.reverse()

    for ev in events:
        post(api, ev)
    wait_for_consumer_caught_up()

    assert len(history_rows(db, vehicle_id)) == 2
    assert str(current_position(db, vehicle_id)[0]) == str(ids[-1])


# Validación del contrato en la API --------------------------------------------
@pytest.mark.parametrize("patch", [
    {"lat": 91},
    {"lon": -181},
    {"speed_kmh": -1},
    {"captured_at": "2026-11-05T10:00:00"},  # sin zona horaria
    {"vehicle_id": ""},
    {"extra": "no permitido"},
])
def test_api_rechaza_eventos_invalidos(api, vehicle_id, patch):
    event = {**make_event(vehicle_id, T0), **patch}
    assert api.post("/v1/positions", json=event).status_code == 422


def test_api_rechaza_fecha_futura(api, vehicle_id):
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    assert api.post("/v1/positions", json=make_event(vehicle_id, future)).status_code == 422
