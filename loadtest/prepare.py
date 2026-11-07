#!/usr/bin/env python3
"""Prepara el conjunto de solicitudes de una ejecución de carga (antes de k6).

La mezcla NO depende del azar: se construye por cuotas exactas en bloques de
20 solicitudes (17 eventos nuevos, 2 reenvíos exactos, 1 inválido = 85/10/5 %).
El azar (con semilla) solo decide el orden dentro de cada bloque, qué evento
anterior se reenvía y las coordenadas. Un último bloque incompleto reparte
sus cupos por el método del mayor resto, así que los totales planificados
quedan registrados en `meta.json` y los realmente enviados los informa la
reconciliación.

- Vehículos estables por ejecución: LT-<run_id>-0001 … (asignación circular).
- Reenvíos: mismo cuerpo exacto (mismo event_id y contenido) de un evento nuevo
  que aparece ANTES en la secuencia (al menos un bloque antes, o antes dentro
  del primer bloque).
- Inválidos: latitud 95.0 (regla del contrato: -90 <= lat <= 90); event_id
  propio, nunca reutilizado.
- Fechas UTC con milisegundos; por vehículo, crecientes; nunca en el futuro.

Uso:
  python loadtest/prepare.py --run-id short-01 --vehicles 20 --rate 10 --duration 30 --seed 7
  python loadtest/prepare.py ... --mode batch --batch-size 20
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

BLOCK = 20
QUOTA = {"new": 17, "dup": 2, "invalid": 1}          # por bloque de 20
CENTER = (4.65, -74.08)                               # Bogotá
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,23}$")
INVALID_RULE = "lat fuera de rango (95.0 > 90)"


def vehicle_id(run_id: str, i: int) -> str:
    return f"LT-{run_id}-{i:04d}"


def canonical(event: dict) -> str:
    return json.dumps(event, sort_keys=True, separators=(",", ":"))


def utc_ms(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def block_kinds(size: int, rng: random.Random, first: bool) -> list[str]:
    """Tipos de un bloque con cuotas exactas (o por mayor resto si es incompleto)."""
    if size == BLOCK:
        counts = dict(QUOTA)
    else:
        raw = {k: v * size / BLOCK for k, v in QUOTA.items()}
        counts = {k: int(v) for k, v in raw.items()}
        for k in sorted(raw, key=lambda k: raw[k] - counts[k], reverse=True)[: size - sum(counts.values())]:
            counts[k] += 1
    kinds = ["new"] * counts["new"] + ["dup"] * counts["dup"] + ["invalid"] * counts["invalid"]
    rng.shuffle(kinds)
    if first:
        # En el primer bloque no hay eventos previos: los reenvíos van al final.
        kinds = [k for k in kinds if k != "dup"] + [k for k in kinds if k == "dup"]
    return kinds


def build(run_id: str, vehicles: int, total: int, seed: int, now: datetime) -> dict:
    if not RUN_ID_RE.match(run_id):
        raise ValueError("run_id: 1-24 caracteres [A-Za-z0-9-], empezando por letra o número")
    if vehicles < 1 or total < 1:
        raise ValueError("vehicles y total deben ser >= 1")
    rng = random.Random(f"{run_id}:{seed}")
    base = (now - timedelta(minutes=10)).replace(microsecond=0)
    positions = [[CENTER[0] + rng.uniform(-0.05, 0.05), CENTER[1] + rng.uniform(-0.05, 0.05)]
                 for _ in range(vehicles)]
    per_vehicle = [0] * vehicles
    requests: list[dict] = []
    new_seqs: list[int] = []                          # secuencias de eventos nuevos
    new_count = 0

    for block_start in range(0, total, BLOCK):
        size = min(BLOCK, total - block_start)
        kinds = block_kinds(size, rng, first=block_start == 0)
        previous_new = [s for s in new_seqs if s < block_start]
        for kind in kinds:
            seq = len(requests)
            if kind == "dup":
                pool = previous_new or new_seqs     # primer bloque: nuevos ya emitidos
                if not pool:
                    kind = "new"                    # imposible reenviar sin previos (total < 2)
                else:
                    src = requests[rng.choice(pool)]
                    requests.append({"seq": seq, "kind": "dup", "source_seq": src["seq"],
                                     "event_id": src["event_id"], "vehicle_id": src["vehicle_id"],
                                     "captured_at": src["captured_at"], "body": src["body"]})
                    continue
            v = new_count % vehicles if kind == "new" else rng.randrange(vehicles)
            event_id = str(uuid.UUID(int=rng.getrandbits(128), version=4))
            if kind == "new":
                new_count += 1
                per_vehicle[v] += 1
                positions[v][0] += rng.uniform(-0.0005, 0.0005)
                positions[v][1] += rng.uniform(-0.0005, 0.0005)
                lat, lon = round(positions[v][0], 6), round(positions[v][1], 6)
                captured = base + timedelta(seconds=per_vehicle[v], milliseconds=rng.randrange(1000))
                speed = round(rng.uniform(0, 80), 1)
            else:  # inválido a propósito
                lat, lon = 95.0, round(CENTER[1], 6)
                captured = base
                speed = 10.0
            event = {"schema_version": 1, "event_id": event_id, "vehicle_id": vehicle_id(run_id, v + 1),
                     "captured_at": utc_ms(captured), "lat": lat, "lon": lon, "speed_kmh": speed}
            item = {"seq": seq, "kind": kind, "event_id": event_id, "vehicle_id": event["vehicle_id"],
                    "captured_at": event["captured_at"], "body": canonical(event)}
            if kind == "invalid":
                item["invalid_rule"] = INVALID_RULE
            requests.append(item)
            if kind == "new":
                new_seqs.append(seq)

    counts = {k: sum(r["kind"] == k for r in requests) for k in ("new", "dup", "invalid")}
    return {"requests": requests, "counts": counts}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run-id", required=True)
    p.add_argument("--vehicles", type=int, required=True)
    p.add_argument("--rate", type=float, required=True, help="solicitudes (o lotes) por segundo")
    p.add_argument("--duration", type=int, required=True, help="segundos")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--mode", choices=["single", "batch"], default="single")
    p.add_argument("--batch-size", type=int, default=20)
    p.add_argument("--out", default=str(Path(__file__).resolve().parent / "runs"))
    args = p.parse_args(argv)

    iterations = int(round(args.rate * args.duration))
    total = iterations * (args.batch_size if args.mode == "batch" else 1)
    now = datetime.now(timezone.utc)
    try:
        built = build(args.run_id, args.vehicles, total, args.seed, now)
    except ValueError as exc:
        p.error(str(exc))
    out = Path(args.out) / args.run_id
    out.mkdir(parents=True, exist_ok=True)
    meta = {
        "run_id": args.run_id, "mode": args.mode, "vehicles": args.vehicles, "rate": args.rate,
        "duration_s": args.duration, "seed": args.seed, "iterations": iterations,
        "batch_size": args.batch_size if args.mode == "batch" else None,
        "planned_requests": total, "planned_counts": built["counts"],
        "planned_unique_identities": built["counts"]["new"],
        "vehicle_ids": [vehicle_id(args.run_id, i + 1) for i in range(args.vehicles)],
        "invalid_rule": INVALID_RULE, "block": {"size": BLOCK, "quota": QUOTA},
        "prepared_at": now.isoformat(),
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (out / "dataset.json").write_text(json.dumps(built["requests"], separators=(",", ":")), encoding="utf-8")
    print(json.dumps({k: meta[k] for k in ("run_id", "mode", "planned_requests", "planned_counts",
                                            "planned_unique_identities")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
