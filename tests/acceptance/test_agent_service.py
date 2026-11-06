"""Servicio de agente con el stack real.

- Servicios reales: portal (nginx) → agente → API de flota → TimescaleDB.
- `test_herramientas_reales_contra_la_api` usa la API real pero un modelo
  GUIONADO (doble): verifica herramientas, cliente y circuito sin proveedor.
- `test_proveedor_real` llama al proveedor real (Anthropic). Es opcional:
  solo corre con AGENT_REAL_PROVIDER_TEST=1 y ANTHROPIC_API_KEY configurada
  en el servicio agent. Consume tokens.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import httpx
import pytest

from conftest import API_URL

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
from agent_fakes import ScriptedModel, agent_settings, last_tool_payloads, answer, tool_calls  # noqa: E402
from fleet_agent.agent import RunState, collect  # noqa: E402
from fleet_agent.main import AgentService  # noqa: E402

PORTAL_URL = os.getenv("PORTAL_URL", "http://localhost:8088")
REAL_PROVIDER = os.getenv("AGENT_REAL_PROVIDER_TEST", "0") == "1"


@pytest.fixture(scope="module")
def portal():
    with httpx.Client(base_url=PORTAL_URL, timeout=90) as client:
        yield client


def test_chat_por_el_mismo_origen_del_portal(portal):
    status = portal.get("/v1/chat/status")
    assert status.status_code == 200
    body = status.json()
    assert body["conversation"] == "independent"
    assert body["limits"]["max_question_chars"] > 0
    # La credencial nunca se expone (solo puede aparecer el nombre de la variable).
    assert "sk-ant" not in status.text

    check = portal.get("/v1/chat/fleet-check")
    assert check.status_code == 200 and check.json()["ok"] is True
    assert check.json()["circuit"]["state"] == "closed"

    if not body["configured"]:
        resp = portal.post("/v1/chat", json={"question": "¿Qué zonas están activas?"})
        assert resp.status_code == 503
        assert resp.json()["status"] == "not_configured"
        assert resp.json()["warnings"][0]["code"] == "agent_not_configured"


def test_herramientas_reales_contra_la_api():
    """Modelo guionado (doble) + herramientas, cliente y circuito reales contra la API real."""
    async def scenario():
        model = ScriptedModel(script=[
            tool_calls(("consultar_zonas", {"only_demo": True}), ("consultar_alertas", {}),
                       ("consultar_posiciones", {"only_stale": True, "limit": 5})),
            lambda messages: answer(str([p["status"] for p in last_tool_payloads(messages)])),
        ])
        svc = AgentService(agent_settings(fleet_api_url=API_URL, fleet_read_timeout_s=10,
                                          fleet_connect_timeout_s=3), model)
        state = RunState()
        try:
            await svc.agent.run("prueba", state)
        finally:
            await svc.aclose()
        return state, svc.breaker.snapshot()

    state, circuit = asyncio.run(scenario())
    records = {r.name: r for r in state.records}
    assert state.status == "answered" and circuit["state"] == "closed"

    zones = records["consultar_zonas"]
    assert zones.status == "ok" and zones.source == "GET /v1/zones"
    assert {"DEMO-Z01", "DEMO-Z02", "DEMO-Z03", "DEMO-Z04"} <= set(zones.zone_ids)

    alerts = records["consultar_alertas"]
    assert alerts.status in {"ok", "no_matches"}
    assert alerts.source_time and alerts.source_time.endswith("Z")
    assert "alerts_total" in alerts.counts

    positions = records["consultar_posiciones"]
    assert positions.source == "GET /v1/vehicles"
    assert positions.counts["returned"] <= 5
    if positions.counts["matching"] > 5:
        assert positions.truncated and any(w["code"] == "results_truncated" for w in positions.warnings)

    gathered = collect(state, max_items=50)
    assert {s["source"] for s in gathered["sources"]} == {
        "GET /v1/zones", "GET /v1/alerts", "GET /v1/vehicles"}


@pytest.mark.skipif(not REAL_PROVIDER, reason="opcional: AGENT_REAL_PROVIDER_TEST=1 y credencial real")
def test_proveedor_real(portal):
    if not portal.get("/v1/chat/status").json()["configured"]:
        pytest.skip("el servicio agent no tiene credencial configurada")
    resp = portal.post("/v1/chat", json={"question": "¿Qué zonas de demostración están activas?"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "answered" and body["answer"]
    assert any(t["name"] == "consultar_zonas" and t["status"] == "ok" for t in body["tools"])
    assert "DEMO-Z01" in body["zone_ids"]
    assert any(s["time_utc"] for s in body["sources"])
