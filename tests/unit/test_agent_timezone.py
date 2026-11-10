"""Fechas del asistente en la zona horaria de presentación (America/Bogota).

Los datos y los cálculos siguen en UTC; solo el texto para el usuario se
convierte, una vez, en el agente (`fleet_agent.timefmt.LocalClock`).
Dobles: API de flota simulada y modelo guionado; herramientas, cliente,
circuito y armado de la respuesta reales.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import SystemMessage

from agent_fakes import (
    FakeFleetApi, ScriptedModel, agent_settings, alerts_body, default_data, last_tool_payloads,
    stop_alert, summary_answer, tool_call, vehicle,
)
from fleet_agent.agent import system_prompt
from fleet_agent.main import AgentService, create_app
from fleet_agent.timefmt import LocalClock

POSITIONS = "consultar_posiciones"
ALERTS = "consultar_alertas"
READ_AT = "2026-11-09T15:26:28.000000Z"
QUERY_AT = "2026-11-09T15:30:34.000000Z"
BOGOTA = "America/Bogota"


# -- conversión -------------------------------------------------------------------

def test_lectura_utc_se_presenta_en_bogota_con_la_zona_explicita():
    clock = LocalClock(BOGOTA)
    at = datetime(2026, 11, 9, 15, 26, 28, tzinfo=timezone.utc)
    assert clock.format(at) == "09/11/2026 10:26:28 America/Bogota"


def test_la_conversion_respeta_el_cambio_de_dia():
    clock = LocalClock(BOGOTA)
    at = datetime(2026, 11, 9, 2, 0, 0, tzinfo=timezone.utc)
    assert clock.format(at) == "08/11/2026 21:00:00 America/Bogota"


def test_cualquier_desfase_de_origen_da_el_mismo_instante_local():
    clock = LocalClock(BOGOTA)
    utc = datetime(2026, 11, 9, 15, 26, 28, tzinfo=timezone.utc)
    madrid = utc.astimezone(timezone(timedelta(hours=1)))
    assert clock.format(madrid) == clock.format(utc)


def test_fecha_sin_zona_y_zona_desconocida_se_rechazan():
    with pytest.raises(ValueError, match="sin zona|no tiene zona"):
        LocalClock(BOGOTA).format(datetime(2026, 11, 9, 15, 26, 28))
    with pytest.raises(ValueError, match="desconocida"):
        LocalClock("America/NoExiste")


# -- herramientas y respuesta --------------------------------------------------------

def _ask(script, question, data=None, **cfg):
    """POST /v1/chat con el arranque completo de la aplicación (lifespan)."""
    api = FakeFleetApi(data)
    model = ScriptedModel(script=script)
    app = create_app(agent_settings(**cfg), model=model, transport=api.transport())
    with TestClient(app) as client:
        body = client.post("/v1/chat", json={"question": question}).json()
    return body, model


def _positions_data() -> dict:
    data = default_data()
    data["/v1/vehicles"] = {"generated_at": QUERY_AT, "count": 1,
                            "vehicles": [vehicle("BOG-001", READ_AT, 0.0)]}
    return data


def test_posiciones_conservan_utc_agregan_hora_local_y_antiguedad_de_246_s():
    body, model = _ask([tool_call(POSITIONS, {"vehicle_ids": ["BOG-001"]}), summary_answer],
                       "¿Dónde está BOG-001?", data=_positions_data(), stale_after_s=120.0)

    assert body["status"] == "answered"
    assert body["display_timezone"] == BOGOTA
    # Lo que vio el modelo: valores ya convertidos, más los UTC originales.
    [payload] = last_tool_payloads(model.seen[1])
    datos = payload["datos"]
    assert datos["zona_horaria"] == BOGOTA
    assert datos["consultado_en_utc"] == QUERY_AT
    assert datos["consultado_en_local"] == "09/11/2026 10:30:34 America/Bogota"
    [row] = datos["vehiculos"]
    assert row["captured_at_utc"] == READ_AT                       # original intacto
    assert row["captured_at_local"] == "09/11/2026 10:26:28 America/Bogota"
    assert row["age_s"] == 246                                      # calculada en UTC
    assert row["stale"] is True                                     # 246 s > 120 s

    # La respuesta: fuentes y evidencias con el mismo instante en ambas formas.
    [source] = body["sources"]
    assert source["time_utc"] == QUERY_AT
    assert source["time_local"] == "09/11/2026 10:30:34 America/Bogota"
    [tool] = body["tools"]
    assert tool["source_time_utc"] == QUERY_AT
    assert tool["source_time_local"] == source["time_local"]
    [ev] = body["evidence"]["vehicles"]
    assert ev["captured_at_utc"] == READ_AT
    assert ev["captured_at_local"] == "09/11/2026 10:26:28 America/Bogota"
    assert ev["age_s"] == 246 and ev["stale"] is True
    # La advertencia de dato desactualizado se conserva.
    assert any(w["code"] == "stale_data" and "BOG-001" in w["message"] for w in body["warnings"])


def test_la_antiguedad_no_depende_de_la_zona_de_presentacion():
    body, model = _ask([tool_call(POSITIONS, {"vehicle_ids": ["BOG-001"]}), summary_answer],
                       "¿Dónde está BOG-001?", data=_positions_data(), stale_after_s=300.0,
                       display_timezone="Asia/Tokyo")
    [row] = last_tool_payloads(model.seen[1])[0]["datos"]["vehiculos"]
    assert row["age_s"] == 246 and row["stale"] is False            # 246 s <= 300 s
    assert row["captured_at_local"] == "10/11/2026 00:26:28 Asia/Tokyo"
    assert body["display_timezone"] == "Asia/Tokyo"
    assert not any(w["code"] == "stale_data" for w in body["warnings"])


def test_alertas_y_evidencia_insuficiente_con_hora_local():
    data = default_data()
    alert = stop_alert("BOG-007", "DEMO-Z01", 1500)
    alert.update(started_at="2026-11-09T01:40:00.000000Z", last_sample_at="2026-11-09T02:00:00.000000Z")
    body_alerts = alerts_body(alerts=[alert], insufficient=[{
        "vehicle_id": "BOG-009", "reason": "stale_last_sample", "detail": "Última muestra antigua",
        "last_sample_at": "2026-11-09T01:50:00.000000Z"}])
    body_alerts["evaluated_at"] = "2026-11-09T02:00:30.000000Z"
    data["/v1/alerts"] = body_alerts
    body, model = _ask([tool_call(ALERTS), summary_answer], "¿Hay alertas?", data=data)

    datos = last_tool_payloads(model.seen[1])[0]["datos"]
    assert datos["evaluado_en_utc"] == "2026-11-09T02:00:30.000000Z"
    assert datos["evaluado_en_local"] == "08/11/2026 21:00:30 America/Bogota"
    [a] = body["evidence"]["alerts"]
    assert a["started_at_utc"] == "2026-11-09T01:40:00.000000Z"
    assert a["started_at_local"] == "08/11/2026 20:40:00 America/Bogota"
    assert a["last_sample_at_local"] == "08/11/2026 21:00:00 America/Bogota"
    [i] = body["evidence"]["insufficient_evidence"]
    assert i["last_sample_at_utc"] == "2026-11-09T01:50:00.000000Z"
    assert i["last_sample_at_local"] == "08/11/2026 20:50:00 America/Bogota"
    assert body["sources"][0]["time_local"] == "08/11/2026 21:00:30 America/Bogota"


def test_las_instrucciones_piden_la_hora_local_copiada_y_no_conversiones():
    _, model = _ask([tool_call(POSITIONS), summary_answer], "¿Dónde están?", data=_positions_data())
    prompt = model.seen[0][0]
    assert isinstance(prompt, SystemMessage)
    assert prompt.content == system_prompt(BOGOTA)
    assert "America/Bogota" in prompt.content
    assert "*_local" in prompt.content and "No conviertas zonas horarias" in prompt.content
    assert "fecha y hora UTC" not in prompt.content


def test_zona_de_presentacion_invalida_detiene_el_arranque():
    # AgentService se construye en el arranque (lifespan) de la aplicación.
    with pytest.raises(ValueError, match="desconocida"):
        AgentService(agent_settings(display_timezone="Marte/Base"), ScriptedModel(script=[]),
                     transport=FakeFleetApi().transport())
