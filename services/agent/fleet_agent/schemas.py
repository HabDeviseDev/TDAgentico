"""Contrato HTTP del chat."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ChatStatus = Literal[
    "answered",          # respuesta final del modelo (puede llevar advertencias)
    "limit_reached",     # se agotaron los pasos del agente
    "model_refused",     # el modelo declinó responder
    "timeout",           # se superó la duración total de la solicitud
    "not_configured",    # falta proveedor/modelo/credencial
    "busy",              # demasiadas solicitudes de chat simultáneas
    "model_error",       # el proveedor del modelo falló
    "internal_error",
]


class ChatRequest(BaseModel):
    # Límite duro del contrato; el límite efectivo es AGENT_MAX_QUESTION_CHARS.
    question: str = Field(..., min_length=1, max_length=4000)


class Warning(BaseModel):
    code: str
    message: str
    tool: str | None = None


class ToolCallOut(BaseModel):
    name: str
    arguments: dict[str, Any]
    status: str
    partial: bool
    truncated: bool
    duration_ms: int
    attempts: int
    source: str | None
    source_time_utc: str | None
    source_time_local: str | None = None
    counts: dict[str, Any]
    error: dict[str, Any] | None


class SourceOut(BaseModel):
    tool: str
    source: str
    time_utc: str | None
    # Mismo instante en la zona de presentación (ChatResponse.display_timezone).
    time_local: str | None = None
    status: str


class Evidence(BaseModel):
    alerts: list[dict[str, Any]] = []
    vehicles: list[dict[str, Any]] = []
    zones: list[dict[str, Any]] = []
    insufficient_evidence: list[dict[str, Any]] = []


class Usage(BaseModel):
    steps: int
    max_steps: int
    tool_calls: int
    max_tool_calls: int
    duration_ms: int


class ChatResponse(BaseModel):
    request_id: str
    status: ChatStatus
    answer: str
    provider: str
    model: str
    # Zona IANA de los campos *_local y del texto de la respuesta. Los *_utc
    # conservan el valor original de la API.
    display_timezone: str
    tools: list[ToolCallOut] = []
    sources: list[SourceOut] = []
    evidence: Evidence = Evidence()
    vehicle_ids: list[str] = []
    zone_ids: list[str] = []
    warnings: list[Warning] = []
    usage: Usage
