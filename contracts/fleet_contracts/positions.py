"""Contrato del evento de posición (fleet.positions.v1).

Este módulo lo comparten la API, el consumidor y las pruebas para que
todos validen y serialicen el evento exactamente igual.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

TOPIC = "fleet.positions.v1"
DLQ_TOPIC = "fleet.positions.dlq"
SCHEMA_VERSION = 1

# Tolerancia para relojes de dispositivos adelantados.
MAX_FUTURE_SKEW = timedelta(minutes=5)


class PositionEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    schema_version: Literal[1] = SCHEMA_VERSION
    event_id: UUID
    vehicle_id: str = Field(min_length=1, max_length=64)
    captured_at: AwareDatetime
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    speed_kmh: float = Field(ge=0, le=300)

    @field_validator("vehicle_id")
    @classmethod
    def _strip_vehicle_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("vehicle_id no puede estar vacío")
        return value

    @field_validator("captured_at")
    @classmethod
    def _to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)

    def canonical_json(self) -> bytes:
        """Serialización estable: mismas claves, mismo orden, misma fecha."""
        doc = {
            "schema_version": self.schema_version,
            "event_id": str(self.event_id),
            "vehicle_id": self.vehicle_id,
            "captured_at": self.captured_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "lat": self.lat,
            "lon": self.lon,
            "speed_kmh": self.speed_kmh,
        }
        return json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def payload_hash(self) -> bytes:
        return hashlib.sha256(self.canonical_json()).digest()


def check_not_in_future(event: PositionEvent, now: datetime | None = None) -> None:
    """Solo la API aplica esta regla; el consumidor no la repite para no
    rechazar reprocesos por diferencias de reloj."""
    now = now or datetime.now(timezone.utc)
    if event.captured_at > now + MAX_FUTURE_SKEW:
        raise ValueError(
            f"captured_at está más de {int(MAX_FUTURE_SKEW.total_seconds() // 60)} "
            "minutos en el futuro"
        )
