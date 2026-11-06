"""Modelos de respuesta. Todas las fechas salen en UTC con sufijo Z."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, field_serializer


def to_utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class VehiclePosition(BaseModel):
    vehicle_id: str
    event_id: str
    captured_at: datetime
    lat: float
    lon: float
    speed_kmh: float
    updated_at: datetime

    @field_serializer("captured_at", "updated_at")
    def _utc(self, value: datetime) -> str:
        return to_utc_iso(value)


class VehiclesResponse(BaseModel):
    generated_at: datetime
    count: int
    vehicles: list[VehiclePosition]

    @field_serializer("generated_at")
    def _utc(self, value: datetime) -> str:
        return to_utc_iso(value)


class SnapshotPayload(VehiclesResponse):
    """Datos del evento SSE `snapshot`: estado completo actual."""

    type: Literal["snapshot"] = "snapshot"
    sequence: int


class StatusPayload(BaseModel):
    """Datos del evento SSE `status`: salud de la lectura de la BD."""

    type: Literal["status"] = "status"
    status: Literal["ok", "degraded"]
    generated_at: datetime
    detail: str | None = None
    last_snapshot_at: datetime | None = None

    @field_serializer("generated_at", "last_snapshot_at")
    def _utc(self, value: datetime | None) -> str | None:
        return to_utc_iso(value) if value is not None else None
