"""Regla de detención dentro de zonas críticas (dominio puro y determinista).

No accede a la BD ni al reloj: recibe las muestras, las zonas, los
parámetros y la hora de evaluación (`now`). Con las mismas entradas
siempre produce el mismo resultado.

Definiciones
------------
- Muestra de detención: velocidad <= `max_speed_kmh`.
- Tramo de detención en una zona: secuencia de muestras consecutivas
  (ordenadas por `captured_at`) en la que todas
    * son muestras de detención,
    * están dentro de la zona (distancia al centro <= radio),
    * están separadas de la anterior por <= `max_gap_s` segundos, y
    * están a <= `max_displacement_m` metros de la PRIMERA muestra del tramo.
  Una muestra de movimiento, fuera de la zona o tras una separación mayor
  rompe el tramo. Una muestra que excede el desplazamiento rompe el tramo
  anterior y empieza uno nuevo (sigue siendo una detención en la zona).
- Continuidad: no hay datos entre dos muestras; se infiere que el vehículo
  siguió detenido entre ellas solo si la separación es <= `max_gap_s`.
- Duración observada: `captured_at` de la última muestra del tramo menos la
  de la primera. El paso del tiempo sin muestras nuevas no la aumenta.
- Alerta: el tramo actual (el que contiene la última muestra del vehículo)
  dura MÁS de `alert_after_s` (exactamente el umbral no alerta).
- Evidencia insuficiente: la última muestra tiene más de
  `max_sample_age_s` segundos respecto a `now`. No se emite alerta.
- Empates de `captured_at`: se usa una sola muestra por instante, la de
  mayor `event_id`, igual que vehicle_current_position (el consumidor
  conserva el mayor `(captured_at, event_id)`).
- Inicio desconocido: si el tramo empieza en la primera muestra disponible
  y esa muestra está a <= `max_gap_s` del inicio de la ventana histórica,
  una muestra anterior (fuera de la ventana) podría continuarlo. La
  duración se informa como mínimo observado.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Sequence

EARTH_RADIUS_M = 6_371_008.8  # radio medio de la Tierra (IUGG)

# Espacio de nombres fijo para los identificadores de alerta (uuid5).
ALERT_NAMESPACE = uuid.UUID("6f1d3c2e-8a4b-5c7d-9e0f-1a2b3c4d5e6f")

REASON_STALE = "stale_last_sample"
REASON_MAX_VEHICLES = "max_vehicles"
REASON_MAX_ROWS = "max_rows"


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia de gran círculo en metros (fórmula del semiverseno)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


@dataclass(frozen=True)
class Zone:
    zone_id: str
    name: str
    center_lat: float
    center_lon: float
    radius_m: float
    is_demo: bool = False

    def contains(self, lat: float, lon: float) -> bool:
        return haversine_m(self.center_lat, self.center_lon, lat, lon) <= self.radius_m


@dataclass(frozen=True)
class Sample:
    vehicle_id: str
    event_id: str
    captured_at: datetime
    lat: float
    lon: float
    speed_kmh: float


@dataclass(frozen=True)
class StopRuleParams:
    max_speed_kmh: float = 3.0
    max_displacement_m: float = 50.0
    max_gap_s: float = 120.0
    max_sample_age_s: float = 120.0
    alert_after_s: float = 20 * 60
    window_s: float = 2 * 60 * 60


@dataclass(frozen=True)
class StopAlert:
    alert_id: str
    vehicle_id: str
    zone_id: str
    zone_name: str
    started_at: datetime          # primera muestra del tramo observado
    last_sample_at: datetime
    observed_duration_s: float
    start_unknown: bool
    sample_count: int
    lat: float                    # posición de la primera muestra del tramo
    lon: float


@dataclass(frozen=True)
class InsufficientEvidence:
    vehicle_id: str
    reason: str
    detail: str
    last_sample_at: datetime


@dataclass(frozen=True)
class VehicleEvaluation:
    vehicle_id: str
    alerts: tuple[StopAlert, ...] = ()
    insufficient: InsufficientEvidence | None = None


@dataclass(frozen=True)
class FleetEvaluation:
    evaluated_at: datetime
    window_start: datetime
    params: StopRuleParams
    complete: bool
    incomplete_reasons: tuple[str, ...]
    vehicles_in_window: int
    vehicles_evaluated: int
    vehicles_not_evaluated: int
    vehicles_without_recent_samples: int
    zones: tuple[Zone, ...]
    alerts: tuple[StopAlert, ...] = field(default_factory=tuple)
    insufficient: tuple[InsufficientEvidence, ...] = field(default_factory=tuple)


# -- muestras -----------------------------------------------------------------

def one_sample_per_instant(samples: Iterable[Sample]) -> list[Sample]:
    """Ordena por captured_at y deja una muestra por instante: la de mayor
    event_id (UUID en minúsculas: el orden de texto coincide con el de uuid
    en PostgreSQL). Las muestras duplicadas quedan una sola vez."""
    best: dict[datetime, Sample] = {}
    for s in samples:
        cur = best.get(s.captured_at)
        if cur is None or s.event_id.lower() > cur.event_id.lower():
            best[s.captured_at] = s
    return [best[t] for t in sorted(best)]


def _is_stop(s: Sample, p: StopRuleParams) -> bool:
    return s.speed_kmh <= p.max_speed_kmh


def _gap_s(a: Sample, b: Sample) -> float:
    return (b.captured_at - a.captured_at).total_seconds()


def current_segment_start(samples: Sequence[Sample], zone: Zone,
                          p: StopRuleParams) -> int | None:
    """Índice de la primera muestra del tramo que contiene la última muestra,
    o None si la última muestra no es una detención dentro de `zone`.

    `samples` debe venir de `one_sample_per_instant`. Primero retrocede desde
    el final mientras se cumplen velocidad, zona y continuidad (lo anterior
    a ese punto rompe cualquier tramo); luego recorre hacia adelante
    aplicando el desplazamiento respecto a la primera muestra del tramo.
    """
    n = len(samples)
    k = n
    while k > 0:
        s = samples[k - 1]
        if not (_is_stop(s, p) and zone.contains(s.lat, s.lon)):
            break
        if k < n and _gap_s(s, samples[k]) > p.max_gap_s:
            break
        k -= 1
    if k == n:
        return None
    start = k
    for i in range(k + 1, n):
        first, s = samples[start], samples[i]
        if haversine_m(first.lat, first.lon, s.lat, s.lon) > p.max_displacement_m:
            start = i
    return start


def alert_id(vehicle_id: str, zone_id: str, start_event_id: str | None) -> str:
    """Estable para la misma detención y zona: depende del vehículo, la zona
    y la primera muestra del tramo. Con inicio desconocido no hay primera
    muestra fija (la ventana avanza), así que se usa un marcador fijo."""
    anchor = start_event_id if start_event_id is not None else "inicio-desconocido"
    return str(uuid.uuid5(ALERT_NAMESPACE, f"{vehicle_id}|{zone_id}|{anchor}"))


def evaluate_vehicle(vehicle_id: str, samples: Iterable[Sample], zones: Sequence[Zone],
                     p: StopRuleParams, now: datetime,
                     window_start: datetime | None = None) -> VehicleEvaluation:
    ordered = one_sample_per_instant(samples)
    if not ordered:
        return VehicleEvaluation(vehicle_id)
    last = ordered[-1]
    age_s = (now - last.captured_at).total_seconds()
    if age_s > p.max_sample_age_s:
        return VehicleEvaluation(vehicle_id, insufficient=InsufficientEvidence(
            vehicle_id=vehicle_id,
            reason=REASON_STALE,
            # Sin la antigüedad exacta: el texto no cambia en cada evaluación.
            detail=(f"La última muestra supera la antigüedad máxima de {p.max_sample_age_s:g} s: "
                    "no se puede confirmar su estado actual"),
            last_sample_at=last.captured_at,
        ))
    if not _is_stop(last, p):
        return VehicleEvaluation(vehicle_id)

    alerts = []
    for zone in zones:
        start = current_segment_start(ordered, zone, p)
        if start is None:
            continue
        first = ordered[start]
        duration_s = _gap_s(first, last)
        if duration_s <= p.alert_after_s:
            continue
        start_unknown = (start == 0 and window_start is not None
                         and (first.captured_at - window_start).total_seconds() <= p.max_gap_s)
        alerts.append(StopAlert(
            alert_id=alert_id(vehicle_id, zone.zone_id, None if start_unknown else first.event_id),
            vehicle_id=vehicle_id,
            zone_id=zone.zone_id,
            zone_name=zone.name,
            started_at=first.captured_at,
            last_sample_at=last.captured_at,
            observed_duration_s=duration_s,
            start_unknown=start_unknown,
            sample_count=len(ordered) - start,
            lat=first.lat,
            lon=first.lon,
        ))
    return VehicleEvaluation(vehicle_id, alerts=tuple(alerts))


# -- flota --------------------------------------------------------------------

def apply_row_limit(rows: Sequence[Sample], max_rows: int) -> tuple[list[Sample], bool]:
    """`rows` viene ordenado por vehículo y pide `max_rows + 1` filas. Si hay
    más de `max_rows`, el último vehículo pudo quedar con historial cortado:
    se descarta completo para no evaluarlo con datos parciales."""
    if len(rows) <= max_rows:
        return list(rows), False
    kept = list(rows[:max_rows])
    cut_vehicle = rows[max_rows].vehicle_id
    while kept and kept[-1].vehicle_id == cut_vehicle:
        kept.pop()
    return kept, True


def evaluate_fleet(rows: Sequence[Sample], zones: Sequence[Zone], p: StopRuleParams,
                   now: datetime, window_start: datetime, *,
                   vehicles_in_window: int, max_vehicles: int, max_rows: int,
                   vehicles_without_recent_samples: int = 0) -> FleetEvaluation:
    """Evalúa todos los vehículos de `rows` (ordenadas por vehículo).

    `vehicles_in_window` es el total de vehículos con muestras en la ventana;
    si se evaluaron menos (límites de vehículos o filas), el resultado se
    marca como parcial con el motivo.
    """
    kept, rows_truncated = apply_row_limit(rows, max_rows)
    by_vehicle: dict[str, list[Sample]] = {}
    for s in kept:
        by_vehicle.setdefault(s.vehicle_id, []).append(s)

    alerts: list[StopAlert] = []
    insufficient: list[InsufficientEvidence] = []
    for vehicle_id in sorted(by_vehicle):
        ev = evaluate_vehicle(vehicle_id, by_vehicle[vehicle_id], zones, p, now, window_start)
        alerts.extend(ev.alerts)
        if ev.insufficient is not None:
            insufficient.append(ev.insufficient)

    reasons = []
    if vehicles_in_window > max_vehicles:
        reasons.append(REASON_MAX_VEHICLES)
    if rows_truncated:
        reasons.append(REASON_MAX_ROWS)
    evaluated = len(by_vehicle)
    not_evaluated = max(0, vehicles_in_window - evaluated)
    alerts.sort(key=lambda a: (-a.observed_duration_s, a.vehicle_id, a.zone_id))
    return FleetEvaluation(
        evaluated_at=now,
        window_start=window_start,
        params=p,
        complete=not reasons and not_evaluated == 0,
        incomplete_reasons=tuple(reasons),
        vehicles_in_window=vehicles_in_window,
        vehicles_evaluated=evaluated,
        vehicles_not_evaluated=not_evaluated,
        vehicles_without_recent_samples=vehicles_without_recent_samples,
        zones=tuple(zones),
        alerts=tuple(alerts),
        insufficient=tuple(insufficient),
    )
