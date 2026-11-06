"""Zonas y alertas de detención con el stack real (API → Kafka → BD → API/SSE).

Cada prueba crea su propia zona lejos de las zonas de demostración y la
elimina al terminar; los vehículos son TEST-* nuevos.
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from conftest import API_URL, DATABASE_URL, make_event, wait_until
from sse_client import SSEClient

READ_TIMEOUT_S = float(os.getenv("DB_READ_TIMEOUT_S", "3"))
EVAL_INTERVAL_S = float(os.getenv("ALERTS_EVAL_INTERVAL_S", "5"))


@pytest.fixture
def test_zone(db):
    zone_id = f"TEST-{uuid.uuid4().hex[:8]}"
    # Al sur de Bogotá, lejos de las zonas de demostración.
    lat, lon = 4.30 + (uuid.uuid4().int % 1000) / 1e5, -74.40
    db.execute(
        "INSERT INTO critical_zones (zone_id, name, center_lat, center_lon, radius_m, active, is_demo) "
        "VALUES (%s, %s, %s, %s, 300, true, false)",
        (zone_id, f"Zona de prueba {zone_id}", lat, lon))
    yield {"zone_id": zone_id, "lat": lat, "lon": lon}
    db.execute("DELETE FROM critical_zones WHERE zone_id = %s", (zone_id,))


def alerts_of(api, vehicle_id: str) -> list[dict]:
    body = api.get("/v1/alerts").json()
    return [a for a in body["alerts"] if a["vehicle_id"] == vehicle_id]


def publish(api, events: list[dict]) -> None:
    for e in events:
        assert api.post("/v1/positions", json=e).status_code == 202


def test_zonas_activas_de_demostracion(api, test_zone):
    resp = api.get("/v1/zones")
    assert resp.status_code == 200
    zones = {z["zone_id"]: z for z in resp.json()["zones"]}
    assert {"DEMO-Z01", "DEMO-Z02", "DEMO-Z03", "DEMO-Z04"} <= set(zones)
    assert "DEMO-Z05" not in zones          # inactiva
    assert zones["DEMO-Z01"]["is_demo"] is True
    assert zones[test_zone["zone_id"]]["radius_m"] == 300


def test_alerta_y_retiro_por_evento_atrasado(api, db, vehicle_id, test_zone):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    at = {"lat": test_zone["lat"], "lon": test_zone["lon"]}
    stopped = [make_event(vehicle_id, now - timedelta(seconds=s), speed_kmh=0.0, **at)
               for s in range(25 * 60, -1, -30)]
    publish(api, stopped)

    wait_until(lambda: len(alerts_of(api, vehicle_id)) == 1, message="alerta en GET /v1/alerts")
    [alert] = alerts_of(api, vehicle_id)
    assert alert["zone_id"] == test_zone["zone_id"]
    assert alert["observed_duration_s"] == 25 * 60
    assert alert["start_unknown"] is False
    assert alert["last_sample_at"].startswith(now.strftime("%Y-%m-%dT%H:%M:%S"))
    body = api.get("/v1/alerts").json()
    assert body["parameters"]["alert_after_s"] == 1200

    current_before = db.execute(
        "SELECT event_id FROM vehicle_current_position WHERE vehicle_id = %s",
        (vehicle_id,)).fetchone()

    with SSEClient(API_URL) as sse:
        # Al conectar llega el resultado completo, que incluye la alerta.
        sse.wait_for(lambda t, d: t == "alerts"
                     and any(a["vehicle_id"] == vehicle_id for a in d["alerts"]), timeout=15)

        # Evento atrasado de movimiento hace 10 min: no cambia la posición actual.
        late = make_event(vehicle_id, now - timedelta(minutes=10, seconds=15),
                          speed_kmh=35.0, **at)
        publish(api, [late])
        _, result = sse.wait_for(
            lambda t, d: t == "alerts"
            and not any(a["vehicle_id"] == vehicle_id for a in d["alerts"]),
            timeout=EVAL_INTERVAL_S + 30)
        assert result["complete"] is True

    assert alerts_of(api, vehicle_id) == []
    current_after = db.execute(
        "SELECT event_id FROM vehicle_current_position WHERE vehicle_id = %s",
        (vehicle_id,)).fetchone()
    assert current_after == current_before


def test_historial_bloqueado_da_503_degrada_alertas_y_se_recupera(api, db):
    with SSEClient(API_URL) as sse:
        sse.wait_for(lambda t, d: t == "alerts", timeout=EVAL_INTERVAL_S + 15)

        locker = psycopg.connect(DATABASE_URL)  # mantiene la transacción
        try:
            locker.execute("LOCK TABLE position_history IN ACCESS EXCLUSIVE MODE")
            start = time.monotonic()
            resp = api.get("/v1/alerts")
            elapsed = time.monotonic() - start
            assert resp.status_code == 503
            assert resp.json()["detail"] == "La base de datos no respondió a tiempo"
            assert READ_TIMEOUT_S <= elapsed < READ_TIMEOUT_S + 3

            _, degraded = sse.wait_for(
                lambda t, d: t == "alerts_status" and d["status"] == "degraded",
                timeout=EVAL_INTERVAL_S + READ_TIMEOUT_S + 10)
            assert degraded["detail"] == "La base de datos no respondió a tiempo"
            assert degraded["last_evaluation_at"] is not None
        finally:
            locker.rollback()
            locker.close()

        sse.wait_for(lambda t, d: t == "alerts_status" and d["status"] == "ok",
                     timeout=EVAL_INTERVAL_S + READ_TIMEOUT_S + 10)
        _, recovered = sse.wait_for(lambda t, d: t == "alerts", timeout=5)
        assert "alerts" in recovered and "complete" in recovered

    assert api.get("/v1/alerts").status_code == 200


def insufficient_of(body: dict, vehicle_id: str) -> dict | None:
    return next((i for i in body["insufficient_evidence"] if i["vehicle_id"] == vehicle_id), None)


def test_muestras_futuras_no_cuentan_hasta_su_hora(api, db, vehicle_id, test_zone):
    """Regresión: 19,5 min hasta el presente + 1 min futuro no alerta; al
    avanzar el reloj, las muestras que eran futuras participan."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    at = {"lat": test_zone["lat"], "lon": test_zone["lon"]}
    future_only = f"{vehicle_id}-F"
    stale = f"{vehicle_id}-S"
    regression = [make_event(vehicle_id, now + timedelta(seconds=s), speed_kmh=0.0, **at)
                  for s in range(-19 * 60 - 30, 61, 30)]
    publish(api, regression)
    publish(api, [make_event(future_only, now + timedelta(minutes=3), speed_kmh=0.0, **at)])
    publish(api, [make_event(stale, now - timedelta(minutes=5), speed_kmh=0.0, **at),
                  make_event(stale, now + timedelta(minutes=2), speed_kmh=0.0, **at)])

    def processed():
        body = api.get("/v1/alerts").json()
        return insufficient_of(body, future_only) and insufficient_of(body, stale)
    wait_until(processed, timeout=20, message="vehículos con muestras futuras evaluados")

    body = api.get("/v1/alerts").json()
    assert datetime.fromisoformat(body["evaluated_at"].replace("Z", "+00:00")) < now + timedelta(seconds=60)
    assert alerts_of(api, vehicle_id) == []
    assert not any(a["vehicle_id"] == vehicle_id for a in body["alerts"])
    assert insufficient_of(body, vehicle_id) is None        # su última muestra evaluable es vigente

    f = insufficient_of(body, future_only)
    assert f["reason"] == "only_future_samples" and f["last_sample_at"] is None
    assert body["coverage"]["vehicles_with_only_future_samples"] >= 1

    s = insufficient_of(body, stale)
    assert s["reason"] == "stale_last_sample"
    assert s["last_sample_at"].startswith((now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S"))

    # Las futuras se conservan en el historial.
    assert db.execute("SELECT count(*) FROM position_history WHERE vehicle_id = %s AND captured_at > %s",
                      (vehicle_id, now)).fetchone()[0] == 2

    # Al pasar now + 60 s, el tramo llega a 20,5 min con las muestras que eran futuras.
    wait_until(lambda: len(alerts_of(api, vehicle_id)) == 1, timeout=120,
               message="alerta cuando las muestras futuras ya son evaluables")
    [alert] = alerts_of(api, vehicle_id)
    assert alert["observed_duration_s"] == 20 * 60 + 30
    assert alert["last_sample_at"].startswith((now + timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%S"))
