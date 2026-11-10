"""Contratos de las respuestas de la API de flota que usa el agente.

Reflejan los esquemas de la API (`services/api/app/vehicles/schemas.py` y
`services/api/app/alerts/schemas.py`) y reutilizan `VehicleId` del contrato
de ingesta (`fleet_contracts`). Se validan en modo estricto sobre el JSON
recibido, ANTES de dar el intento por bueno en el circuit breaker:
- campos obligatorios y tipos exactos (sin convertir "4.6" en número ni 1 en
  booleano);
- fechas ISO-8601 con zona horaria (se normalizan a UTC);
- rangos (latitud, longitud, velocidad, radio, conteos) y coherencia entre
  conteos y listas.

Campos adicionales se ignoran: la API puede agregar campos sin romper al
agente; quitar o cambiar uno obligatorio sí es un contrato inválido.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fleet_contracts import VehicleId
from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, model_validator

# zone_id es `text` en critical_zones; el agente acota la longitud.
ZONE_ID_MAX_LENGTH = 128
ZoneId = Annotated[str, Field(min_length=1, max_length=ZONE_ID_MAX_LENGTH)]


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


UtcDatetime = Annotated[AwareDatetime, AfterValidator(_utc)]
Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]
NonNegative = Annotated[int, Field(ge=0)]


class _Contract(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, allow_inf_nan=False, frozen=True)


# -- GET /v1/vehicles --------------------------------------------------------------

class VehiclePosition(_Contract):
    vehicle_id: VehicleId
    event_id: str
    captured_at: UtcDatetime
    lat: Latitude
    lon: Longitude
    speed_kmh: Annotated[float, Field(ge=0)]
    updated_at: UtcDatetime


class VehiclesResponse(_Contract):
    generated_at: UtcDatetime
    count: NonNegative
    vehicles: list[VehiclePosition]

    @model_validator(mode="after")
    def _count(self) -> "VehiclesResponse":
        if self.count != len(self.vehicles):
            raise ValueError("count no coincide con la cantidad de vehículos")
        return self


# -- GET /v1/zones -------------------------------------------------------------------

class CriticalZone(_Contract):
    zone_id: ZoneId
    name: str
    center_lat: Latitude
    center_lon: Longitude
    radius_m: Annotated[float, Field(gt=0)]
    is_demo: bool


class ZonesResponse(_Contract):
    generated_at: UtcDatetime
    count: NonNegative
    zones: list[CriticalZone]

    @model_validator(mode="after")
    def _count(self) -> "ZonesResponse":
        if self.count != len(self.zones):
            raise ValueError("count no coincide con la cantidad de zonas")
        return self


# -- GET /v1/alerts --------------------------------------------------------------------

class AlertParameters(_Contract):
    max_speed_kmh: Annotated[float, Field(ge=0)]
    max_displacement_m: Annotated[float, Field(ge=0)]
    max_gap_s: Annotated[float, Field(gt=0)]
    max_sample_age_s: Annotated[float, Field(gt=0)]
    alert_after_s: Annotated[float, Field(gt=0)]
    window_s: Annotated[float, Field(gt=0)]
    max_vehicles: Annotated[int, Field(gt=0)]
    max_rows: Annotated[int, Field(gt=0)]


class Coverage(_Contract):
    vehicles_in_window: NonNegative
    vehicles_evaluated: NonNegative
    vehicles_not_evaluated: NonNegative
    vehicles_without_recent_samples: NonNegative
    vehicles_with_only_future_samples: NonNegative


class StopAlert(_Contract):
    alert_id: Annotated[str, Field(min_length=1)]
    vehicle_id: VehicleId
    zone_id: ZoneId
    zone_name: str
    started_at: UtcDatetime
    last_sample_at: UtcDatetime
    observed_duration_s: Annotated[float, Field(ge=0)]
    start_unknown: bool
    sample_count: Annotated[int, Field(ge=1)]
    lat: Latitude
    lon: Longitude


class InsufficientEvidence(_Contract):
    vehicle_id: VehicleId
    reason: Annotated[str, Field(min_length=1)]
    detail: str
    last_sample_at: UtcDatetime | None


class AlertsResponse(_Contract):
    evaluated_at: UtcDatetime
    window_start: UtcDatetime
    parameters: AlertParameters
    complete: bool
    incomplete_reasons: list[str]
    coverage: Coverage
    zone_ids: list[ZoneId]
    alert_count: NonNegative
    alerts: list[StopAlert]
    insufficient_evidence_count: NonNegative
    insufficient_evidence: list[InsufficientEvidence]

    @model_validator(mode="after")
    def _consistent(self) -> "AlertsResponse":
        if self.alert_count != len(self.alerts):
            raise ValueError("alert_count no coincide con la cantidad de alertas")
        if self.insufficient_evidence_count != len(self.insufficient_evidence):
            raise ValueError("insufficient_evidence_count no coincide con la lista")
        if self.window_start > self.evaluated_at:
            raise ValueError("window_start posterior a evaluated_at")
        return self


RESPONSE_CONTRACTS: dict[str, type[_Contract]] = {
    "/v1/vehicles": VehiclesResponse,
    "/v1/zones": ZonesResponse,
    "/v1/alerts": AlertsResponse,
}


def to_utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
