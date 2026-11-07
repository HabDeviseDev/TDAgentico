"""Lógica pura de la reconciliación (sin base de datos ni Kafka).

Entradas:
- el conjunto preparado (`dataset.json`): qué se planeó enviar, con el cuerpo exacto;
- los puntos `lt_result` exportados por k6 (`--out json`): qué pasó con cada
  secuencia, en una o varias ejecuciones (p. ej. un reenvío posterior);
- una instantánea de la BD restringida a los vehículos de la ejecución.

Salida: manifiesto por solicitud, estado por identidad (event_id) y
discrepancias. Los reenvíos (`dup`) comparten identidad con su original y no
suman identidades únicas esperadas.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

OUTCOME_ORDER = ["confirmed", "expected_rejection", "unexpected", "ambiguous"]


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass(frozen=True)
class Observation:
    seq: int
    outcome: str       # confirmed | expected_rejection | ambiguous | unexpected
    status: str        # HTTP o estado del lote (accepted, rejected, retryable, timeout, …)
    time: datetime
    source: str


def load_points(paths: Iterable[Path]) -> list[Observation]:
    """Lee los puntos `lt_result` de las salidas JSON de k6 (una línea por punto)."""
    out: list[Observation] = []
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if '"lt_result"' not in line:
                    continue
                doc = json.loads(line)
                if doc.get("type") != "Point" or doc.get("metric") != "lt_result":
                    continue
                tags = doc["data"]["tags"]
                out.append(Observation(seq=int(tags["seq"]), outcome=tags["outcome"], status=tags["status"],
                                       time=parse_time(doc["data"]["time"]), source=Path(path).name))
    return out


@dataclass
class ManifestRow:
    seq: int
    kind: str
    event_id: str
    vehicle_id: str
    captured_at: str
    body: str
    observations: list[Observation] = field(default_factory=list)

    @property
    def final(self) -> str:
        """Resultado consolidado de la solicitud."""
        outcomes = {o.outcome for o in self.observations}
        if not outcomes:
            return "not_sent"
        for o in OUTCOME_ORDER:
            if o in outcomes:
                return o
        return "unexpected"


def build_manifest(requests: list[dict], observations: list[Observation]) -> list[ManifestRow]:
    rows = [ManifestRow(r["seq"], r["kind"], r["event_id"], r["vehicle_id"], r["captured_at"], r["body"])
            for r in requests]
    by_seq = {r.seq: r for r in rows}
    for obs in sorted(observations, key=lambda o: o.time):
        row = by_seq.get(obs.seq)
        if row is not None:
            row.observations.append(obs)
    return rows


@dataclass
class Identity:
    event_id: str
    kind: str                       # "valid" o "invalid"
    expected: dict[str, Any]
    seqs: list[int] = field(default_factory=list)
    outcomes: set[str] = field(default_factory=set)
    first_confirmed: datetime | None = None

    @property
    def status(self) -> str:
        if self.kind == "invalid":
            return "invalid"
        if "confirmed" in self.outcomes:
            return "confirmed"
        if "ambiguous" in self.outcomes:
            return "ambiguous"
        if "unexpected" in self.outcomes:
            return "unexpected"
        return "not_sent"


def identities(manifest: list[ManifestRow]) -> dict[str, Identity]:
    out: dict[str, Identity] = {}
    for row in manifest:
        ident = out.get(row.event_id)
        if ident is None:
            ident = out[row.event_id] = Identity(row.event_id, "invalid" if row.kind == "invalid" else "valid",
                                                 json.loads(row.body))
        ident.seqs.append(row.seq)
        for o in row.observations:
            ident.outcomes.add(o.outcome)
            if o.outcome == "confirmed" and (ident.first_confirmed is None or o.time < ident.first_confirmed):
                ident.first_confirmed = o.time
    return out


@dataclass
class DbSnapshot:
    ingested: dict[str, dict]                 # event_id -> fila de ingested_events
    history: dict[str, list[dict]]            # event_id -> filas de position_history
    current: dict[str, dict]                  # vehicle_id -> fila de vehicle_current_position
    conflicts: dict[str, int]                 # event_id -> filas en event_id_conflicts
    expected_hashes: dict[str, bytes] = field(default_factory=dict)


def _key(captured_at: datetime, event_id: str) -> tuple:
    return (captured_at, str(event_id).lower())


def _close(a: float, b: float, tol: float) -> bool:
    return math.isclose(float(a), float(b), rel_tol=0, abs_tol=tol)


def evaluate(ids: dict[str, Identity], db: DbSnapshot, vehicles: list[str]) -> dict:
    """Comprueba la BD contra lo esperado. Devuelve conteos y discrepancias."""
    disc: list[dict] = []

    def add(kind: str, event_id: str | None, detail: str) -> None:
        disc.append({"type": kind, "event_id": event_id, "detail": detail})

    counts = {"identities": len(ids), "confirmed": 0, "ambiguous": 0, "ambiguous_persisted": 0,
              "invalid": 0, "not_sent": 0, "unexpected": 0, "persisted_ok": 0}
    ambiguous_report = []
    for ident in ids.values():
        status = ident.status
        counts[status] = counts.get(status, 0) + 1
        hist = db.history.get(ident.event_id, [])
        in_ingested = ident.event_id in db.ingested
        persisted = in_ingested or bool(hist)
        if status == "invalid":
            if persisted:
                add("invalid_persisted", ident.event_id, "un evento inválido aparece en la BD")
            continue
        if status in ("confirmed", "ambiguous", "unexpected") and len(hist) > 1:
            add("duplicate_history", ident.event_id, f"{len(hist)} filas en position_history")
        if db.conflicts.get(ident.event_id):
            add("event_id_conflict", ident.event_id, f"{db.conflicts[ident.event_id]} conflicto(s) registrados")
        if status == "ambiguous":
            ambiguous_report.append({"event_id": ident.event_id, "persisted": persisted, "seqs": ident.seqs})
            if persisted:
                counts["ambiguous_persisted"] += 1
        if status == "not_sent" and persisted:
            add("persisted_without_send", ident.event_id, "persistido sin un envío registrado")
        if status == "confirmed":
            if not in_ingested:
                add("missing_ingested", ident.event_id, "recepción confirmada pero ausente en ingested_events")
            if not hist:
                add("missing_history", ident.event_id, "recepción confirmada pero ausente en position_history")
        if hist:
            problems = _content_problems(ident.expected, hist[0])
            for p in problems:
                add("content_mismatch", ident.event_id, p)
            if status == "confirmed" and in_ingested and not problems:
                counts["persisted_ok"] += 1
        expected_hash = db.expected_hashes.get(ident.event_id)
        if expected_hash is not None and in_ingested and bytes(db.ingested[ident.event_id]["payload_hash"]) != expected_hash:
            add("payload_hash_mismatch", ident.event_id, "payload_hash distinto del contenido enviado")

    # Filas de la BD que no corresponden a ninguna identidad de la ejecución.
    for event_id in set(db.history) | set(db.ingested):
        if event_id not in ids:
            add("persisted_unknown", event_id, "fila de un vehículo de la ejecución que no está en el conjunto")

    # Posición actual: el mayor (captured_at, event_id) de lo persistido por vehículo.
    by_vehicle: dict[str, list[dict]] = {}
    for rows in db.history.values():
        for r in rows:
            by_vehicle.setdefault(r["vehicle_id"], []).append(r)
    checked = 0
    for v in vehicles:
        rows = by_vehicle.get(v, [])
        current = db.current.get(v)
        if not rows:
            if current is not None:
                add("current_without_history", None, f"{v}: posición actual sin historial de la ejecución")
            continue
        best = max(rows, key=lambda r: _key(r["captured_at"], r["event_id"]))
        checked += 1
        if current is None:
            add("current_missing", str(best["event_id"]), f"{v}: sin posición actual")
        elif str(current["event_id"]).lower() != str(best["event_id"]).lower():
            add("current_mismatch", str(current["event_id"]),
                f"{v}: actual {current['event_id']} @ {current['captured_at']}, esperado "
                f"{best['event_id']} @ {best['captured_at']}")
    counts["vehicles_checked"] = checked
    return {"counts": counts, "discrepancies": disc, "ambiguous": ambiguous_report}


def _content_problems(expected: dict, row: dict) -> list[str]:
    problems = []
    if row["vehicle_id"] != expected["vehicle_id"]:
        problems.append(f"vehicle_id {row['vehicle_id']} != {expected['vehicle_id']}")
    if row["captured_at"] != parse_time(expected["captured_at"]):
        problems.append(f"captured_at {row['captured_at']} != {expected['captured_at']}")
    if not _close(row["lat"], expected["lat"], 1e-9) or not _close(row["lon"], expected["lon"], 1e-9):
        problems.append(f"coordenadas ({row['lat']}, {row['lon']}) != ({expected['lat']}, {expected['lon']})")
    if not _close(row["speed_kmh"], expected["speed_kmh"], 1e-3):   # columna `real` (float32)
        problems.append(f"speed_kmh {row['speed_kmh']} != {expected['speed_kmh']}")
    return problems


def percentiles(values: list[float], ps=(50, 95, 99)) -> dict[str, float | None]:
    if not values:
        return {f"p{p}": None for p in ps}
    s = sorted(values)
    out = {}
    for p in ps:
        k = max(0, min(len(s) - 1, math.ceil(p / 100 * len(s)) - 1))   # percentil por rango más cercano
        out[f"p{p}"] = round(s[k], 3)
    return out


def sent_counts(manifest: list[ManifestRow]) -> dict[str, dict[str, int]]:
    """Cantidades realmente ejecutadas por tipo y resultado consolidado."""
    out: dict[str, dict[str, int]] = {}
    for row in manifest:
        bucket = out.setdefault(row.kind, {})
        bucket[row.final] = bucket.get(row.final, 0) + 1
        bucket["sent"] = bucket.get("sent", 0) + (1 if row.observations else 0)
    return out
