"""Dobles para las pruebas del agente (no llaman a servicios reales).

- `ScriptedModel`: modelo de chat LangChain determinista. Cada paso del
  guion es un AIMessage o una función de los mensajes recibidos.
- `FakeFleetApi`: API de flota simulada sobre `httpx.MockTransport`. Las
  herramientas y el cliente HTTP reales se ejecutan contra ella.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from typing import Any, Callable

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr

from fleet_agent.settings import Settings

EVALUATED_AT = "2026-11-06T20:00:00.000000Z"
FLEET_URL = "http://fleet.test"


def tool_call(name: str, args: dict | None = None, call_id: str = "c1") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args or {}, "id": call_id,
                                              "type": "tool_call"}])


def tool_calls(*calls: tuple[str, dict]) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": n, "args": a, "id": f"c{i}", "type": "tool_call"}
                                             for i, (n, a) in enumerate(calls)])


def answer(text: str, **metadata: Any) -> AIMessage:
    return AIMessage(content=text, response_metadata=metadata)


def last_tool_payloads(messages: list[BaseMessage]) -> list[dict]:
    """Resultados de herramientas del último paso, ya decodificados."""
    out = []
    for m in reversed(messages):
        if not isinstance(m, ToolMessage):
            break
        out.insert(0, json.loads(m.content))
    return out


class ScriptedModel(BaseChatModel):
    script: list[Any]
    delay_s: float = 0.0
    error: Any = None
    _calls: int = PrivateAttr(default=0)
    _seen: list = PrivateAttr(default_factory=list)
    _bound_tools: list = PrivateAttr(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    @property
    def calls(self) -> int:
        return self._calls

    @property
    def seen(self) -> list[list[BaseMessage]]:
        return self._seen

    @property
    def bound_tool_names(self) -> list[str]:
        return [t.name for t in self._bound_tools]

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001
        self._bound_tools = list(tools)
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001
        raise NotImplementedError("usar ainvoke")

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001
        self._seen.append(list(messages))
        index = self._calls
        self._calls += 1
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.error is not None:
            raise self.error
        step = self.script[min(index, len(self.script) - 1)]
        message = step(messages) if callable(step) else step
        return ChatResult(generations=[ChatGeneration(message=message)])


def vehicle(vehicle_id: str, captured_at: str, speed: float = 0.0) -> dict:
    return {"vehicle_id": vehicle_id, "event_id": "e-" + vehicle_id, "captured_at": captured_at,
            "lat": 4.6, "lon": -74.1, "speed_kmh": speed, "updated_at": captured_at}


def default_data() -> dict[str, dict]:
    return {
        "/v1/vehicles": {"generated_at": EVALUATED_AT, "count": 2, "vehicles": [
            vehicle("BOG-001", "2026-11-06T19:59:30.000000Z", 32.0),
            vehicle("BOG-002", "2026-11-06T19:40:00.000000Z", 0.0),
        ]},
        "/v1/zones": {"generated_at": EVALUATED_AT, "count": 2, "zones": [
            {"zone_id": "DEMO-Z01", "name": "Demo · Centro logístico", "center_lat": 4.623,
             "center_lon": -74.115, "radius_m": 400.0, "is_demo": True},
            {"zone_id": "Z-REAL", "name": "Patio real", "center_lat": 4.7,
             "center_lon": -74.03, "radius_m": 250.0, "is_demo": False},
        ]},
        "/v1/alerts": alerts_body(),
    }


def alerts_body(*, alerts: list[dict] | None = None, insufficient: list[dict] | None = None,
                complete: bool = True) -> dict:
    alerts = [stop_alert("BOG-007", "DEMO-Z01", 1500)] if alerts is None else alerts
    insufficient = [] if insufficient is None else insufficient
    return {
        "evaluated_at": EVALUATED_AT,
        "window_start": "2026-11-06T18:00:00.000000Z",
        "parameters": {"max_speed_kmh": 3.0, "max_displacement_m": 50.0, "max_gap_s": 120.0,
                       "max_sample_age_s": 120.0, "alert_after_s": 1200.0, "window_s": 7200.0,
                       "max_vehicles": 1000, "max_rows": 100000},
        "complete": complete,
        "incomplete_reasons": [] if complete else ["max_vehicles"],
        "coverage": {"vehicles_in_window": 3 if complete else 5, "vehicles_evaluated": 3,
                     "vehicles_not_evaluated": 0 if complete else 2,
                     "vehicles_without_recent_samples": 0, "vehicles_with_only_future_samples": 0},
        "zone_ids": ["DEMO-Z01"],
        "alert_count": len(alerts),
        "alerts": alerts,
        "insufficient_evidence_count": len(insufficient),
        "insufficient_evidence": insufficient,
    }


def stop_alert(vehicle_id: str, zone_id: str, seconds: float, start_unknown: bool = False) -> dict:
    return {"alert_id": f"a-{vehicle_id}-{zone_id}", "vehicle_id": vehicle_id, "zone_id": zone_id,
            "zone_name": "Demo · Centro logístico", "started_at": "2026-11-06T19:35:00.000000Z",
            "last_sample_at": "2026-11-06T19:59:50.000000Z", "observed_duration_s": seconds,
            "start_unknown": start_unknown, "sample_count": 50, "lat": 4.623, "lon": -74.115}


def stale_detail() -> dict:
    return {"vehicle_id": "BOG-009", "reason": "stale_last_sample",
            "detail": "La última muestra supera la antigüedad máxima de 120 s: no se puede confirmar su estado actual",
            "last_sample_at": "2026-11-06T19:50:00.000000Z"}


class FakeFleetApi:
    """mode: ok | error503 | timeout | connect | hang | invalid_json | http404."""

    def __init__(self, data: dict[str, dict] | None = None):
        self.data = data or default_data()
        self.mode = "ok"
        self.queue: list[str] = []      # modos para las próximas peticiones, en orden
        self.requests: list[str] = []
        self.entered: asyncio.Event | None = None   # se crea dentro del loop
        self.cancelled = 0

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(f"{request.method} {request.url.path}")
        mode = self.queue.pop(0) if self.queue else self.mode
        if mode == "timeout":
            raise httpx.ReadTimeout("timeout simulado", request=request)
        if mode == "connect":
            raise httpx.ConnectError("conexión rechazada", request=request)
        if mode == "hang":
            if self.entered is None:
                self.entered = asyncio.Event()
            self.entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled += 1
                raise
        if mode == "error503":
            return httpx.Response(503, json={"detail": "La base de datos no respondió a tiempo"})
        if mode == "http404":
            return httpx.Response(404, json={"detail": "Not Found"})
        if mode == "invalid_json":
            return httpx.Response(200, content=b"<html>no es json</html>")
        body = self.data.get(request.url.path)
        if body is None:
            return httpx.Response(404, json={"detail": "Not Found"})
        return httpx.Response(200, json=body)


def agent_settings(**overrides: Any) -> Settings:
    base = dict(provider="anthropic", model="modelo-simulado", anthropic_api_key="",
                fleet_api_url=FLEET_URL, fleet_connect_timeout_s=0.5, fleet_read_timeout_s=0.5,
                fleet_retries=1, fleet_retry_backoff_s=0.0, breaker_failure_threshold=3,
                breaker_recovery_s=30.0, breaker_half_open_max_calls=1, max_question_chars=500,
                max_concurrent_chats=4, request_timeout_s=5.0, max_steps=4, max_tool_calls=5,
                max_tool_items=25, max_answer_chars=4000, max_evidence_items=50,
                stale_after_s=120.0)
    base.update(overrides)
    return dataclasses.replace(Settings(), **base)


class ManualClock:
    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


Factory = Callable[..., Any]
