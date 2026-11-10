"""Contratos de respuesta de la API e identificadores exactos en el agente.

Dobles: API de flota simulada (httpx.MockTransport) y modelo guionado. El
cliente HTTP, el circuit breaker, los contratos y las herramientas son reales.
"""

from __future__ import annotations

import asyncio
import copy
import json
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from agent_fakes import (
    FLEET_URL, FakeFleetApi, ManualClock, ScriptedModel, agent_settings, alerts_body, answer,
    default_data, stop_alert, summary_answer, tool_call, vehicle,
)
from fleet_agent.breaker import BreakerState, CircuitBreaker
from fleet_agent.fleet_client import FleetApiError, FleetClient
from fleet_agent.main import AgentService, create_app
from fleet_agent.tools import AlertsArgs, PositionsArgs
from fleet_contracts import PositionEvent

SECRET = "SECRETO-INTERNO-123"


def client_for(api: FakeFleetApi, *, threshold: int = 3, retries: int = 2,
               clock: ManualClock | None = None) -> FleetClient:
    breaker = CircuitBreaker("fleet-api", failure_threshold=threshold, recovery_timeout_s=30,
                             clock=clock or ManualClock())
    return FleetClient(FLEET_URL, breaker, connect_timeout_s=0.5, read_timeout_s=0.5, retries=retries,
                       retry_backoff_s=0.0, max_response_bytes=1_000_000, transport=api.transport())


def run(coro):
    return asyncio.run(coro)


# -- respuestas fuera de contrato ------------------------------------------------------

def _del(path: list) -> Callable[[dict], None]:
    def mutate(body: dict) -> None:
        target = body
        for key in path[:-1]:
            target = target[key]
        del target[path[-1]]
    return mutate


def _set(path: list, value: Any) -> Callable[[dict], None]:
    def mutate(body: dict) -> None:
        target = body
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
    return mutate


INVALID_CASES = [
    # campos ausentes
    ("/v1/alerts", "falta evaluated_at", _del(["evaluated_at"])),
    ("/v1/alerts", "falta parameters.alert_after_s", _del(["parameters", "alert_after_s"])),
    ("/v1/alerts", "falta zone_id de una alerta", _del(["alerts", 0, "zone_id"])),
    ("/v1/vehicles", "falta vehicles", _del(["vehicles"])),
    ("/v1/zones", "falta radius_m", _del(["zones", 0, "radius_m"])),
    # tipos incorrectos
    ("/v1/vehicles", "lat como texto", _set(["vehicles", 0, "lat"], "4.6")),
    ("/v1/vehicles", "speed_kmh nulo", _set(["vehicles", 0, "speed_kmh"], None)),
    ("/v1/zones", "is_demo como número", _set(["zones", 0, "is_demo"], 1)),
    ("/v1/alerts", "complete como texto", _set(["complete"], "true")),
    ("/v1/alerts", "alerts como objeto", _set(["alerts"], {"0": SECRET})),
    ("/v1/vehicles", "vehicle_id numérico", _set(["vehicles", 0, "vehicle_id"], 7)),
    # fechas inválidas
    ("/v1/alerts", "fecha imposible", _set(["evaluated_at"], "2026-13-40T25:00:00Z")),
    ("/v1/alerts", "fecha sin zona horaria", _set(["evaluated_at"], "2026-11-06T20:00:00")),
    ("/v1/vehicles", "fecha en texto libre", _set(["vehicles", 0, "captured_at"], SECRET)),
    ("/v1/alerts", "last_sample_at no es fecha",
     _set(["alerts", 0, "last_sample_at"], 1730923200)),
    # rangos y coherencia
    ("/v1/vehicles", "latitud fuera de rango", _set(["vehicles", 0, "lat"], 123.0)),
    ("/v1/zones", "count distinto de la lista", _set(["count"], 5)),
    ("/v1/alerts", "alert_count distinto de la lista", _set(["alert_count"], 9)),
]


@pytest.mark.parametrize("path,case,mutate", INVALID_CASES, ids=[c[1] for c in INVALID_CASES])
def test_respuesta_fuera_de_contrato_es_invalid_response_y_cuenta_como_fallo(path, case, mutate):
    async def scenario():
        api = FakeFleetApi()
        mutate(api.data[path])
        client = client_for(api)
        with pytest.raises(FleetApiError) as exc:
            await client.get(path)
        await client.aclose()
        return exc.value, api, client.breaker

    err, api, breaker = run(scenario())
    assert err.kind == "invalid_response"
    assert err.counts_as_failure is True and err.retryable is False
    assert len(api.requests) == 1                          # no se reintenta
    assert breaker.snapshot()["consecutive_failures"] == 1  # no se registró un éxito
    public = json.dumps(err.public(), ensure_ascii=False)
    # El error público no incluye campos, valores ni detalles de validación.
    assert SECRET not in public and "validation" not in public.lower()
    assert err.public()["message"] == "La API de flota devolvió una respuesta que no cumple el contrato esperado"


def test_respuestas_validas_se_aceptan_incluidos_enteros_en_campos_reales():
    async def scenario():
        api = FakeFleetApi()
        api.data["/v1/zones"]["zones"][0]["radius_m"] = 400          # entero JSON
        api.data["/v1/vehicles"]["vehicles"][0]["extra"] = "nuevo"   # campo adicional
        api.data["/v1/alerts"]["evaluated_at"] = "2026-11-06T15:00:00-05:00"  # otra zona horaria
        api.data["/v1/alerts"]["window_start"] = "2026-11-06T13:00:00-05:00"
        client = client_for(api)
        zones, _ = await client.get("/v1/zones")
        vehicles, _ = await client.get("/v1/vehicles")
        alerts, _ = await client.get("/v1/alerts")
        await client.aclose()
        return zones, vehicles, alerts, client.breaker

    zones, vehicles, alerts, breaker = run(scenario())
    assert zones.zones[0].radius_m == 400.0
    assert vehicles.vehicles[0].vehicle_id == "BOG-001"
    assert alerts.evaluated_at.utcoffset().total_seconds() == 0 and alerts.evaluated_at.hour == 20
    assert breaker.state is BreakerState.CLOSED


def test_respuestas_invalidas_abren_el_circuito():
    async def scenario():
        clock = ManualClock()
        api = FakeFleetApi()
        api.data["/v1/alerts"]["evaluated_at"] = "no-es-fecha"
        client = client_for(api, threshold=2, retries=2, clock=clock)
        for _ in range(2):
            with pytest.raises(FleetApiError):
                await client.get("/v1/alerts")
        opened = client.breaker.state
        calls = len(api.requests)
        with pytest.raises(FleetApiError) as exc:
            await client.get("/v1/alerts")
        rejected_kind, calls_after = exc.value.kind, len(api.requests)
        # Cuando la API vuelve a cumplir el contrato, la prueba de recuperación cierra.
        api.data = default_data()
        clock.advance(30)
        body, _ = await client.get("/v1/alerts")
        await client.aclose()
        return opened, calls, rejected_kind, calls_after, client.breaker.state, body

    opened, calls, rejected_kind, calls_after, final_state, body = run(scenario())
    assert opened is BreakerState.OPEN
    assert calls == 2                        # un intento por consulta: no se reintenta
    assert rejected_kind == "circuit_open" and calls_after == calls
    assert final_state is BreakerState.CLOSED and body.alert_count == 1


def test_herramienta_unavailable_con_advertencia_y_sin_datos_internos():
    api = FakeFleetApi()
    api.data["/v1/alerts"]["alerts"][0]["started_at"] = SECRET
    model = ScriptedModel(script=[tool_call("consultar_alertas"), summary_answer])
    app = create_app(agent_settings(), model=model, transport=api.transport())
    with TestClient(app) as client:
        resp = client.post("/v1/chat", json={"question": "¿Qué alertas hay?"})
    body = resp.json()
    tool = body["tools"][0]
    assert resp.status_code == 200
    assert tool["status"] == "unavailable"
    assert tool["error"]["kind"] == "invalid_response"
    assert [w["code"] for w in body["warnings"]] == ["invalid_api_response"]
    assert body["evidence"]["alerts"] == [] and body["vehicle_ids"] == []
    assert SECRET not in resp.text
    # El modelo recibió el error genérico, tampoco con el valor interno.
    assert SECRET not in json.dumps([m.content for m in model.seen[1]], ensure_ascii=False)


# -- identificadores exactos ---------------------------------------------------------------

def two_cases_data() -> dict:
    data = default_data()
    data["/v1/vehicles"] = {"generated_at": "2026-11-06T20:00:00.000000Z", "count": 2, "vehicles": [
        vehicle("BOG-001", "2026-11-06T19:59:30.000000Z", 30.0),
        vehicle("bog-001", "2026-11-06T19:59:40.000000Z", 0.0),
    ]}
    data["/v1/alerts"] = alerts_body(alerts=[stop_alert("BOG-001", "DEMO-Z01", 1500),
                                             stop_alert("bog-001", "DEMO-Z01", 1800)])
    return data


@pytest.mark.parametrize("wanted", ["BOG-001", "bog-001"])
def test_regresion_mayusculas_posiciones_y_alertas(wanted):
    async def scenario():
        api = FakeFleetApi(two_cases_data())
        svc = AgentService(agent_settings(), ScriptedModel(script=[answer("x")]),
                           transport=api.transport())
        positions = await svc.tools.execute("consultar_posiciones", {"vehicle_ids": [wanted]})
        alerts = await svc.tools.execute("consultar_alertas", {"vehicle_ids": [wanted]})
        await svc.aclose()
        return positions.record, alerts.record

    positions, alerts = run(scenario())
    assert positions.vehicle_ids == [wanted]
    assert [v["vehicle_id"] for v in positions.evidence["vehicles"]] == [wanted]
    assert positions.counts["matching"] == 1 and positions.counts["not_found"] == 0
    assert alerts.vehicle_ids == [wanted]
    assert [a["vehicle_id"] for a in alerts.evidence["alerts"]] == [wanted]


def test_identificador_con_otras_mayusculas_no_coincide():
    async def scenario():
        api = FakeFleetApi()                       # solo existe BOG-001 y la zona DEMO-Z01
        svc = AgentService(agent_settings(), ScriptedModel(script=[answer("x")]),
                           transport=api.transport())
        positions = await svc.tools.execute("consultar_posiciones", {"vehicle_ids": ["Bog-001"]})
        alerts = await svc.tools.execute("consultar_alertas", {"zone_ids": ["demo-z01"]})
        await svc.aclose()
        return positions.record, alerts.record

    positions, alerts = run(scenario())
    assert positions.status == "no_matches" and positions.vehicle_ids == []
    assert positions.counts["not_found"] == 1
    assert alerts.status == "no_matches" and alerts.zone_ids == []


# -- argumentos frente al contrato de origen ------------------------------------------------

class LegacyVehicleId(BaseModel):
    """Regla de vehicle_id del evento antes de extraerla a fleet_contracts."""

    model_config = ConfigDict(extra="forbid")
    vehicle_id: str = Field(min_length=1, max_length=64)

    @field_validator("vehicle_id")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("vehicle_id no puede estar vacío")
        return value


CANDIDATES = ["BOG-001", "bog-001", "Camión 7", "BOG/01", "车辆-1", "TEST-0533909c40ae",
              "DEMO-1940-ZONA25", "a" * 64, "  BOG-003  ", " x ", "../etc/passwd",
              "", "   ", "a" * 65, " " + "b" * 64]


def _outcome(fn: Callable[[], Any]) -> tuple[bool, Any]:
    try:
        return True, fn()
    except ValidationError:
        return False, None


def _event(vehicle_id: str) -> dict:
    return {"event_id": "0192f5a8-7c1e-7b3a-9d2f-4e8a1c3b5d70", "vehicle_id": vehicle_id,
            "captured_at": "2026-11-06T20:00:00Z", "lat": 4.6, "lon": -74.1, "speed_kmh": 0}


@pytest.mark.parametrize("candidate", CANDIDATES)
def test_argumentos_admiten_los_mismos_identificadores_que_la_ingesta(candidate):
    ingest = _outcome(lambda: PositionEvent.model_validate(_event(candidate)).vehicle_id)
    legacy = _outcome(lambda: LegacyVehicleId(vehicle_id=candidate).vehicle_id)
    positions = _outcome(lambda: PositionsArgs(vehicle_ids=[candidate]).vehicle_ids[0])
    alerts = _outcome(lambda: AlertsArgs(vehicle_ids=[candidate]).vehicle_ids[0])
    # La ingesta conserva su semántica y el agente acepta exactamente lo mismo.
    assert ingest == legacy == positions == alerts


def test_identificador_valido_de_la_ingesta_llega_exacto_a_la_api_simulada():
    async def scenario():
        data = copy.deepcopy(default_data())
        data["/v1/vehicles"]["vehicles"].append(vehicle("Camión 7", "2026-11-06T19:59:50.000000Z"))
        data["/v1/vehicles"]["count"] = 3
        api = FakeFleetApi(data)
        svc = AgentService(agent_settings(), ScriptedModel(script=[answer("x")]),
                           transport=api.transport())
        result = await svc.tools.execute("consultar_posiciones", {"vehicle_ids": ["Camión 7"]})
        await svc.aclose()
        return result.record

    record = run(scenario())
    assert record.status == "ok" and record.vehicle_ids == ["Camión 7"]
