"""Consulta de vehículos y SSE contra el stack real.

El hub lee la BD cada VEHICLES_POLL_INTERVAL_S (1 s por defecto), así que
tras confirmar la escritura se espera como máximo un par de intervalos.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from conftest import API_URL, history_rows, make_event, wait_for_consumer_caught_up, wait_until
from sse_client import SSEClient, vehicle_in

POLL_INTERVAL_S = float(os.getenv("VEHICLES_POLL_INTERVAL_S", "1"))
PORTAL_URL = os.getenv("PORTAL_URL")
BOGOTA = {"lat": 4.65, "lon": -74.08}


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def post(api, event: dict) -> None:
    resp = api.post("/v1/positions", json=event)
    assert resp.status_code == 202, resp.text


def get_vehicle(api, vehicle_id: str) -> dict | None:
    body = api.get("/v1/vehicles").json()
    return next((v for v in body["vehicles"] if v["vehicle_id"] == vehicle_id), None)


def wait_visible_to_hub(api, vehicle_id: str, event_id: str) -> None:
    """Espera a que la BD tenga el dato confirmado y a que el hub lo haya leído."""
    wait_for_consumer_caught_up()
    wait_until(lambda: (get_vehicle(api, vehicle_id) or {}).get("event_id") == event_id,
               message=f"{vehicle_id} con {event_id} en /v1/vehicles")
    time.sleep(POLL_INTERVAL_S * 2.5)


def test_get_vehicles_devuelve_la_ultima_posicion(api, vehicle_id):
    t = now_utc() - timedelta(minutes=1)
    older = make_event(vehicle_id, t, **BOGOTA, speed_kmh=20.0)
    newer = make_event(vehicle_id, t + timedelta(seconds=30), lat=4.66, lon=-74.07, speed_kmh=0.0)
    post(api, older)
    post(api, newer)
    wait_for_consumer_caught_up()

    resp = api.get("/v1/vehicles")
    assert resp.status_code == 200
    body = resp.json()
    assert body["generated_at"].endswith("Z")
    assert body["count"] == len(body["vehicles"])

    v = get_vehicle(api, vehicle_id)
    assert v is not None
    assert v["event_id"] == newer["event_id"]
    assert (v["lat"], v["lon"], v["speed_kmh"]) == (4.66, -74.07, 0.0)
    assert v["captured_at"].endswith("Z")
    assert datetime.fromisoformat(v["captured_at"].replace("Z", "+00:00")) == t + timedelta(seconds=30)
    assert v["updated_at"].endswith("Z")


def test_sse_envia_snapshot_inicial_con_datos_confirmados(api, vehicle_id):
    event = make_event(vehicle_id, now_utc(), **BOGOTA)
    post(api, event)
    wait_visible_to_hub(api, vehicle_id, event["event_id"])

    with SSEClient(API_URL) as sse:
        assert sse.response.headers["content-type"].startswith("text/event-stream")
        first_type, status = sse.next_event()
        assert first_type == "status" and status["status"] == "ok"
        snapshot = sse.next_snapshot()

    assert snapshot["type"] == "snapshot"
    assert snapshot["generated_at"].endswith("Z")
    assert snapshot["count"] == len(snapshot["vehicles"])
    assert vehicle_in(snapshot, vehicle_id)["event_id"] == event["event_id"]


def test_sse_actualiza_despues_de_persistir_una_posicion(api, db, vehicle_id):
    with SSEClient(API_URL) as sse:
        initial = sse.next_snapshot()
        assert vehicle_in(initial, vehicle_id) is None

        event = make_event(vehicle_id, now_utc(), **BOGOTA, speed_kmh=33.0)
        post(api, event)
        _, snapshot = sse.wait_for(
            lambda t, d: t == "snapshot"
            and (vehicle_in(d, vehicle_id) or {}).get("event_id") == event["event_id"])

    assert snapshot["sequence"] > initial["sequence"]
    # El snapshot proviene de datos ya confirmados en la BD.
    assert db.execute("SELECT event_id::text FROM vehicle_current_position WHERE vehicle_id = %s",
                      (vehicle_id,)).fetchone()[0] == event["event_id"]


def test_sse_conserva_la_posicion_mas_reciente_ante_un_evento_atrasado(api, db, vehicle_id):
    t = now_utc()
    recent = make_event(vehicle_id, t, lat=4.70, lon=-74.04)
    late = make_event(vehicle_id, t - timedelta(minutes=10), lat=4.60, lon=-74.13)

    with SSEClient(API_URL) as sse:
        post(api, recent)
        sse.wait_for(lambda ty, d: ty == "snapshot"
                     and (vehicle_in(d, vehicle_id) or {}).get("event_id") == recent["event_id"])
        post(api, late)
        wait_for_consumer_caught_up()
        time.sleep(POLL_INTERVAL_S * 2.5)

    assert len(history_rows(db, vehicle_id)) == 2  # el atrasado quedó en el historial
    assert get_vehicle(api, vehicle_id)["event_id"] == recent["event_id"]
    with SSEClient(API_URL) as sse:
        current = vehicle_in(sse.next_snapshot(), vehicle_id)
    assert current["event_id"] == recent["event_id"]
    assert (current["lat"], current["lon"]) == (4.70, -74.04)


def test_tras_reconectar_llega_un_snapshot_completo(api, vehicle_id):
    first_vehicle = f"{vehicle_id}-A"
    second_vehicle = f"{vehicle_id}-B"
    e1 = make_event(first_vehicle, now_utc(), **BOGOTA)
    post(api, e1)
    wait_visible_to_hub(api, first_vehicle, e1["event_id"])

    with SSEClient(API_URL) as sse:
        before = sse.next_snapshot()
    assert vehicle_in(before, first_vehicle) is not None

    # Mientras el cliente está desconectado llega otra posición.
    e2 = make_event(second_vehicle, now_utc(), lat=4.68, lon=-74.05)
    post(api, e2)
    wait_visible_to_hub(api, second_vehicle, e2["event_id"])

    with SSEClient(API_URL) as sse:
        after = sse.next_snapshot()
    rest = api.get("/v1/vehicles").json()

    assert vehicle_in(after, first_vehicle)["event_id"] == e1["event_id"]
    assert vehicle_in(after, second_vehicle)["event_id"] == e2["event_id"]
    assert after["sequence"] > before["sequence"]
    # Es el estado completo, no un delta.
    assert {v["vehicle_id"] for v in after["vehicles"]} == {v["vehicle_id"] for v in rest["vehicles"]}


# --- A través del proxy nginx del portal ----------------------------------------
needs_portal = pytest.mark.skipif(not PORTAL_URL, reason="PORTAL_URL no definido")


@needs_portal
def test_portal_entrega_sse_sin_buffering():
    started = time.monotonic()
    # read_timeout corto: si nginx acumulara la respuesta, no llegaría nada.
    with SSEClient(PORTAL_URL, read_timeout=5) as sse:
        assert sse.response.headers["content-type"].startswith("text/event-stream")
        snapshot = sse.next_snapshot(timeout=5)
    assert snapshot["type"] == "snapshot"
    assert time.monotonic() - started < 5


@needs_portal
def test_portal_mantiene_rest_e_ingesta_en_el_mismo_origen(vehicle_id):
    with httpx.Client(base_url=PORTAL_URL, timeout=10) as portal:
        assert portal.get("/").status_code == 200
        assert "text/html" in portal.get("/").headers["content-type"]
        config = portal.get("/config.json").json()
        assert config["staleAfterSeconds"] > 0
        assert portal.get("/v1/vehicles").status_code == 200
        event = make_event(vehicle_id, now_utc(), **BOGOTA)
        assert portal.post("/v1/positions", json=event).status_code == 202
