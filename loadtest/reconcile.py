#!/usr/bin/env python3
"""Reconciliación final de una ejecución de carga (SOLO LECTURA).

Lee el conjunto preparado y los resultados exportados por k6, espera de forma
acotada el procesamiento pendiente y compara con TimescaleDB y Kafka:
- cada evento válido con recepción confirmada está en ingested_events y en
  position_history, una sola vez y con el contenido enviado;
- los inválidos no se persistieron;
- la posición actual de cada vehículo es el mayor (captured_at, event_id);
- los eventos ambiguos (timeout, 503, respuesta perdida) se informan aparte;
- mensajes de la ejecución en la DLQ.

Un lag cero NO basta: la espera termina cuando están todas las identidades
confirmadas Y el lag es cero, o al vencer el tiempo; las comprobaciones se
hacen siempre sobre el contenido de la BD.

No escribe en la BD ni confirma offsets en Kafka. Solo filtra por los
vehículos de la ejecución (LT-<run_id>-NNNN).

Salida: reconcile-report.json, reconcile-report.md y manifest.csv en la
carpeta de la ejecución. Código de salida: 0 sin discrepancias, 1 con
discrepancias, 2 si no se pudo completar.

Uso: python loadtest/reconcile.py --run-id short-01 [--max-wait 120] [--emit-resend]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from reconcile_lib import (  # noqa: E402
    DbSnapshot, build_manifest, evaluate, identities, load_points, percentiles, sent_counts,
)

TOPIC = "fleet.positions.v1"
DLQ = "fleet.positions.dlq"


def db_snapshot(conn, vehicles: list[str]) -> DbSnapshot:
    cur = conn.cursor()
    cur.execute("SELECT event_id::text, vehicle_id, captured_at, payload_hash, ingested_at "
                "FROM ingested_events WHERE vehicle_id = ANY(%s)", (vehicles,))
    ingested = {r[0]: {"vehicle_id": r[1], "captured_at": r[2], "payload_hash": r[3], "ingested_at": r[4]}
                for r in cur.fetchall()}
    cur.execute("SELECT event_id::text, vehicle_id, captured_at, lat, lon, speed_kmh, ingested_at "
                "FROM position_history WHERE vehicle_id = ANY(%s)", (vehicles,))
    history: dict[str, list[dict]] = {}
    for r in cur.fetchall():
        history.setdefault(r[0], []).append({"event_id": r[0], "vehicle_id": r[1], "captured_at": r[2],
                                             "lat": r[3], "lon": r[4], "speed_kmh": r[5], "ingested_at": r[6]})
    cur.execute("SELECT vehicle_id, event_id::text, captured_at FROM vehicle_current_position "
                "WHERE vehicle_id = ANY(%s)", (vehicles,))
    current = {r[0]: {"event_id": r[1], "captured_at": r[2]} for r in cur.fetchall()}
    cur.execute("SELECT c.event_id::text, count(*) FROM event_id_conflicts c "
                "JOIN ingested_events i USING (event_id) WHERE i.vehicle_id = ANY(%s) GROUP BY 1", (vehicles,))
    conflicts = {r[0]: r[1] for r in cur.fetchall()}
    conn.rollback()                                   # solo lectura: no deja transacción abierta
    return DbSnapshot(ingested, history, current, conflicts)


def topic_stats(bootstrap: str, group: str) -> dict | None:
    """Mensajes en el tópico, offsets confirmados por el grupo y lag (solo lectura)."""
    try:
        from confluent_kafka import Consumer, TopicPartition
    except ImportError:
        return None
    probe = Consumer({"bootstrap.servers": bootstrap, "group.id": group, "enable.auto.commit": False})
    try:
        md = probe.list_topics(TOPIC, timeout=10)
        parts = [TopicPartition(TOPIC, p) for p in md.topics[TOPIC].partitions]
        end = committed = lag = 0
        for tp in probe.committed(parts, timeout=10):
            low, high = probe.get_watermark_offsets(TopicPartition(TOPIC, tp.partition), timeout=10)
            done = tp.offset if tp.offset >= 0 else low
            end += high
            committed += done
            lag += high - done
        return {"end": end, "committed": committed, "lag": lag}
    except Exception:  # noqa: BLE001 - Kafka caído: se informa como desconocido
        return None
    finally:
        probe.close()


def consumer_lag(bootstrap: str, group: str) -> int | None:
    stats = topic_stats(bootstrap, group)
    return None if stats is None else stats["lag"]


def dlq_hits(bootstrap: str, markers: list[str], timeout_s: float = 20) -> dict:
    """Recorre la DLQ desde el principio sin unirse a un grupo ni confirmar offsets."""
    try:
        from confluent_kafka import OFFSET_BEGINNING, Consumer, TopicPartition
    except ImportError:
        return {"checked": False, "reason": "confluent_kafka no disponible"}
    c = Consumer({"bootstrap.servers": bootstrap, "group.id": f"reconcile-readonly-{uuid.uuid4().hex}",
                  "enable.auto.commit": False, "enable.partition.eof": True})
    try:
        md = c.list_topics(DLQ, timeout=10)
        if DLQ not in md.topics or md.topics[DLQ].error:
            return {"checked": False, "reason": "no existe el tópico DLQ"}
        parts = list(md.topics[DLQ].partitions)
        pending = set()
        for p in parts:
            low, high = c.get_watermark_offsets(TopicPartition(DLQ, p), timeout=10)
            if high > low:
                pending.add(p)
        c.assign([TopicPartition(DLQ, p, OFFSET_BEGINNING) for p in parts])
        scanned, hits, deadline = 0, [], time.monotonic() + timeout_s
        while pending and time.monotonic() < deadline:
            msg = c.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() == -191:          # _PARTITION_EOF
                    pending.discard(msg.partition())
                continue
            scanned += 1
            value = (msg.value() or b"").decode("utf-8", "replace")
            if any(m in value for m in markers):
                hits.append({"partition": msg.partition(), "offset": msg.offset(), "value": value[:300]})
        return {"checked": not pending, "scanned": scanned, "hits": hits}
    except Exception as exc:  # noqa: BLE001
        return {"checked": False, "reason": repr(exc)}
    finally:
        c.close()


def expected_hashes(ids) -> dict[str, bytes]:
    """payload_hash esperado con el mismo contrato que la API (si está disponible)."""
    try:
        from fleet_contracts import PositionEvent
    except ImportError:
        return {}
    out = {}
    for i in ids.values():
        if i.kind == "valid":
            out[i.event_id] = PositionEvent.model_validate(i.expected).payload_hash()
    return out


def git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              timeout=5).stdout.strip() or None
    except Exception:  # noqa: BLE001
        return os.getenv("GIT_COMMIT")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run-id", required=True)
    p.add_argument("--runs-dir", default=str(Path(__file__).resolve().parent / "runs"))
    p.add_argument("--database-url", default=os.getenv("DATABASE_URL", "postgresql://fleet:fleet@localhost:5432/fleet"))
    p.add_argument("--kafka", default=os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9094"))
    p.add_argument("--group", default=os.getenv("KAFKA_GROUP_ID", "fleet-ingestion"))
    p.add_argument("--max-wait", type=float, default=120, help="segundos máximos esperando procesamiento")
    p.add_argument("--emit-resend", action="store_true",
                   help="escribe dataset-resend.json con los eventos ambiguos (mismo cuerpo exacto)")
    args = p.parse_args(argv)

    run_dir = Path(args.runs_dir) / args.run_id
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    requests = json.loads((run_dir / "dataset.json").read_text(encoding="utf-8"))
    point_files = sorted(run_dir.glob("k6-points-*.json"))
    observations = load_points(point_files)
    manifest = build_manifest(requests, observations)
    ids = identities(manifest)
    vehicles = meta["vehicle_ids"]
    confirmed_ids = {e for e, i in ids.items() if i.status == "confirmed"}

    import psycopg
    started = time.monotonic()
    waits = []
    try:
        # Sesión de solo lectura impuesta por PostgreSQL: cualquier escritura fallaría.
        conn = psycopg.connect(args.database_url, autocommit=False,
                               options="-c default_transaction_read_only=on")
    except Exception as exc:  # noqa: BLE001
        print(f"No se pudo conectar a la BD: {exc}", file=sys.stderr)
        return 2
    with conn:
        while True:
            snap = db_snapshot(conn, vehicles)
            missing = len(confirmed_ids - set(snap.history))
            lag = consumer_lag(args.kafka, args.group)
            waits.append({"t": round(time.monotonic() - started, 1), "missing_confirmed": missing, "lag": lag})
            if missing == 0 and lag == 0:
                break
            if time.monotonic() - started > args.max_wait:
                break
            time.sleep(2)
        snap.expected_hashes = expected_hashes(ids)
        result = evaluate(ids, snap, vehicles)

    final_lag = waits[-1]["lag"]
    if final_lag not in (0, None) or waits[-1]["missing_confirmed"]:
        result["discrepancies"].append({
            "type": "pending_after_wait", "event_id": None,
            "detail": f"tras {waits[-1]['t']} s: faltan {waits[-1]['missing_confirmed']} confirmados, lag {final_lag}"})

    dlq = dlq_hits(args.kafka, [f"LT-{args.run_id}-"])   # el prefijo del vehículo identifica la ejecución
    # Persistencia: desde la primera confirmación HTTP hasta ingested_at en la BD.
    persist_lat = []
    for e in confirmed_ids:
        rows = snap.history.get(e)
        if rows and ids[e].first_confirmed:
            persist_lat.append((rows[0]["ingested_at"] - ids[e].first_confirmed).total_seconds() * 1000)

    counts = sent_counts(manifest)
    report = {
        "run_id": args.run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit() or os.getenv("GIT_COMMIT"),
        "params": {k: meta[k] for k in ("mode", "vehicles", "rate", "duration_s", "seed", "iterations",
                                         "batch_size", "planned_requests")},
        "planned": {"counts": meta["planned_counts"], "unique_identities": meta["planned_unique_identities"]},
        "executed": counts,
        "result_files": [f.name for f in point_files],
        "identities": result["counts"],
        "ambiguous": {"count": len(result["ambiguous"]),
                      "persisted": sum(a["persisted"] for a in result["ambiguous"]),
                      "not_persisted": sum(not a["persisted"] for a in result["ambiguous"]),
                      "items": result["ambiguous"][:50]},
        "dlq": dlq,
        "wait": {"seconds": waits[-1]["t"], "max_wait_s": args.max_wait, "polls": waits[-10:],
                 "final_lag": final_lag},
        "persistence_latency_ms": percentiles(persist_lat),
        "discrepancies": result["discrepancies"][:200],
        "discrepancy_count": len(result["discrepancies"]),
        "ok": not result["discrepancies"] and not (dlq.get("hits")),
    }
    if dlq.get("hits"):
        report["discrepancies"].append({"type": "dlq_messages", "event_id": None,
                                        "detail": f"{len(dlq['hits'])} mensaje(s) de la ejecución en la DLQ"})
        report["discrepancy_count"] += 1

    (run_dir / "reconcile-report.json").write_text(json.dumps(report, indent=2, default=str, ensure_ascii=False),
                                                   encoding="utf-8")
    with open(run_dir / "manifest.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["seq", "kind", "event_id", "vehicle_id", "captured_at", "final", "attempts",
                    "statuses", "persisted", "body"])
        for row in manifest:
            w.writerow([row.seq, row.kind, row.event_id, row.vehicle_id, row.captured_at, row.final,
                        len(row.observations), "|".join(o.status for o in row.observations),
                        row.event_id in snap.history, row.body])
    if args.emit_resend:
        resend, seen = [], set()
        for row in manifest:
            if ids[row.event_id].status == "ambiguous" and row.event_id not in seen:
                seen.add(row.event_id)
                resend.append({"seq": row.seq, "kind": row.kind, "event_id": row.event_id,
                               "vehicle_id": row.vehicle_id, "captured_at": row.captured_at, "body": row.body})
        (run_dir / "dataset-resend.json").write_text(json.dumps(resend), encoding="utf-8")
        report["resend_file"] = {"events": len(resend)}
    (run_dir / "reconcile-report.md").write_text(markdown(report), encoding="utf-8")
    print(markdown(report))
    return 0 if report["ok"] else 1


def markdown(r: dict) -> str:
    lines = [f"# Reconciliación {r['run_id']}", "",
             f"- Generado: {r['generated_at']} · commit `{r['git_commit']}`",
             f"- Parámetros: {json.dumps(r['params'])}",
             f"- Planificado: {json.dumps(r['planned'])}",
             f"- Ejecutado (por tipo y resultado): {json.dumps(r['executed'])}",
             f"- Identidades: {json.dumps(r['identities'])}",
             f"- Ambiguos: {r['ambiguous']['count']} (persistidos {r['ambiguous']['persisted']}, "
             f"no persistidos {r['ambiguous']['not_persisted']})",
             f"- DLQ: {json.dumps({k: v for k, v in r['dlq'].items() if k != 'hits'})}, "
             f"mensajes de la ejecución: {len(r['dlq'].get('hits') or [])}",
             f"- Espera: {r['wait']['seconds']} s (máx. {r['wait']['max_wait_s']}), lag final {r['wait']['final_lag']}",
             f"- Tiempo hasta persistencia (ms): {json.dumps(r['persistence_latency_ms'])}",
             f"- Discrepancias: {r['discrepancy_count']}",
             f"- **Resultado: {'OK' if r['ok'] else 'CON DISCREPANCIAS'}**", ""]
    for d in r["discrepancies"][:20]:
        lines.append(f"  - {d['type']}: {d['event_id']} {d['detail']}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
