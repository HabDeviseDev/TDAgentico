"""Modelos de respuesta de zonas y alertas. Fechas en UTC con sufijo Z."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, PlainSerializer

from ..vehicles.schemas import to_utc_iso
from .rules import FleetEvaluation, StopRuleParams, Zone

UtcDatetime = Annotated[datetime, PlainSerializer(to_utc_iso, return_type=str)]


class ZoneOut(BaseModel):
    zone_id: str
    name: str
    center_lat: float
    center_lon: float
    radius_m: float
    is_demo: bool

    @classmethod
    def of(cls, z: Zone) -> "ZoneOut":
        return cls(zone_id=z.zone_id, name=z.name, center_lat=z.center_lat,
                   center_lon=z.center_lon, radius_m=z.radius_m, is_demo=z.is_demo)


class ZonesResponse(BaseModel):
    generated_at: UtcDatetime
    count: int
    zones: list[ZoneOut]


class AlertParameters(BaseModel):
    max_speed_kmh: float
    max_displacement_m: float
    max_gap_s: float
    max_sample_age_s: float
    alert_after_s: float
    window_s: float
    max_vehicles: int
    max_rows: int


class AlertOut(BaseModel):
    alert_id: str
    vehicle_id: str
    zone_id: str
    zone_name: str
    started_at: UtcDatetime
    last_sample_at: UtcDatetime
    observed_duration_s: float
    start_unknown: bool
    sample_count: int
    lat: float
    lon: float


class InsufficientEvidenceOut(BaseModel):
    vehicle_id: str
    reason: str
    detail: str
    last_sample_at: UtcDatetime


class Coverage(BaseModel):
    vehicles_in_window: int
    vehicles_evaluated: int
    vehicles_not_evaluated: int
    # Vehículos con posición actual pero sin muestras dentro de la ventana:
    # no pueden tener alertas y no se listan uno por uno.
    vehicles_without_recent_samples: int


class AlertsResponse(BaseModel):
    evaluated_at: UtcDatetime
    window_start: UtcDatetime
    parameters: AlertParameters
    complete: bool
    incomplete_reasons: list[str]
    coverage: Coverage
    zone_ids: list[str]
    alert_count: int
    alerts: list[AlertOut]
    insufficient_evidence_count: int
    insufficient_evidence: list[InsufficientEvidenceOut]

    @classmethod
    def of(cls, ev: FleetEvaluation, max_vehicles: int, max_rows: int) -> "AlertsResponse":
        p: StopRuleParams = ev.params
        return cls(
            evaluated_at=ev.evaluated_at,
            window_start=ev.window_start,
            parameters=AlertParameters(
                max_speed_kmh=p.max_speed_kmh, max_displacement_m=p.max_displacement_m,
                max_gap_s=p.max_gap_s, max_sample_age_s=p.max_sample_age_s,
                alert_after_s=p.alert_after_s, window_s=p.window_s,
                max_vehicles=max_vehicles, max_rows=max_rows),
            complete=ev.complete,
            incomplete_reasons=list(ev.incomplete_reasons),
            coverage=Coverage(
                vehicles_in_window=ev.vehicles_in_window,
                vehicles_evaluated=ev.vehicles_evaluated,
                vehicles_not_evaluated=ev.vehicles_not_evaluated,
                vehicles_without_recent_samples=ev.vehicles_without_recent_samples),
            zone_ids=[z.zone_id for z in ev.zones],
            alert_count=len(ev.alerts),
            alerts=[AlertOut(**a.__dict__) for a in ev.alerts],
            insufficient_evidence_count=len(ev.insufficient),
            insufficient_evidence=[InsufficientEvidenceOut(**i.__dict__) for i in ev.insufficient],
        )


class AlertsPayload(AlertsResponse):
    """Datos del evento SSE `alerts`: resultado completo de una evaluación."""

    type: Literal["alerts"] = "alerts"
    sequence: int


class AlertsStatusPayload(BaseModel):
    """Datos del evento SSE `alerts_status`: salud de la evaluación."""

    type: Literal["alerts_status"] = "alerts_status"
    status: Literal["ok", "degraded"]
    generated_at: UtcDatetime
    detail: str | None = None
    # Última evaluación correcta (la que produjo el resultado conservado).
    last_evaluation_at: UtcDatetime | None = None
