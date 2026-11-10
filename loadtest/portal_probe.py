#!/usr/bin/env python3
"""Mide el tiempo hasta la visualización en el portal.

Abre el SSE del portal (nginx → API) como lo hace el navegador y envía N
eventos de sonda (vehículo LT-<run_id>-PROBE, fuera de la lista de vehículos
de la carga). Para cada uno mide:
- recepción: POST → 202 de la API;
- portal: 202 → primer snapshot SSE que contiene ese event_id.
"Portal" incluye consumidor, BD y el intervalo de lectura del hub
(VEHICLES_POLL_INTERVAL_S); no mide el render del navegador.

Uso: python loadtest/portal_probe.py --run-id <run> [--count 10]
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

from reconcile_lib import percentiles


def sse_reader(url: str, out: "queue.Queue[tuple[float, dict]]", stop: threading.Event) -> None:
    with httpx.Client(timeout=httpx.Timeout(connect=5, read=30, write=5, pool=5)) as client:
        with client.stream("GET", url, headers={"Accept": "text/event-stream"}) as resp:
            event, data = None, []
            for line in resp.iter_lines():
                if stop.is_set():
                    return
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].strip())
                elif line == "" and data:
                    if event == "snapshot":
                        out.put((time.monotonic(), json.loads("".join(data))))
                    event, data = None, []


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-id", required=True)
    p.add_argument("--count", type=int, default=10)
    p.add_argument("--api-url", default=os.getenv("API_URL", "http://api:8000"))
    p.add_argument("--portal-url", default=os.getenv("PORTAL_URL", "http://portal"))
    p.add_argument("--runs-dir", default=str(Path(__file__).resolve().parent / "runs"))
    p.add_argument("--timeout", type=float, default=30)
    args = p.parse_args(argv)

    snapshots: "queue.Queue[tuple[float, dict]]" = queue.Queue()
    stop = threading.Event()
    threading.Thread(target=sse_reader, args=(f"{args.portal_url}/v1/vehicles/stream", snapshots, stop),
                     daemon=True).start()
    snapshots.get(timeout=args.timeout)               # snapshot inicial: el SSE está listo

    vehicle = f"LT-{args.run_id}-PROBE"
    samples = []
    with httpx.Client(timeout=10) as api:
        for i in range(args.count):
            event_id = str(uuid.uuid4())
            body = {"schema_version": 1, "event_id": event_id, "vehicle_id": vehicle,
                    "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "lat": 4.65, "lon": -74.08, "speed_kmh": float(i)}
            t0 = time.monotonic()
            resp = api.post(f"{args.api_url}/v1/positions", json=body)
            t_ack = time.monotonic()
            seen = None
            deadline = t_ack + args.timeout
            while seen is None and time.monotonic() < deadline:
                try:
                    t_snap, snap = snapshots.get(timeout=max(0.1, deadline - time.monotonic()))
                except queue.Empty:
                    break
                if any(v["vehicle_id"] == vehicle and v["event_id"] == event_id for v in snap["vehicles"]):
                    seen = t_snap
            samples.append({"event_id": event_id, "status": resp.status_code,
                            "receive_ms": round((t_ack - t0) * 1000, 1),
                            "ack_to_portal_ms": round((seen - t_ack) * 1000, 1) if seen else None})
            time.sleep(1)
    stop.set()
    seen_ms = [s["ack_to_portal_ms"] for s in samples if s["ack_to_portal_ms"] is not None]
    report = {"run_id": args.run_id, "vehicle": vehicle, "count": args.count, "seen": len(seen_ms),
              "receive_ms": percentiles([s["receive_ms"] for s in samples]),
              "ack_to_portal_ms": percentiles(seen_ms), "samples": samples,
              "portal_url": args.portal_url, "measured_at": datetime.now(timezone.utc).isoformat()}
    out = Path(args.runs_dir) / args.run_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "portal-probe.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("count", "seen", "receive_ms", "ack_to_portal_ms")}))
    return 0 if len(seen_ms) == args.count else 1


if __name__ == "__main__":
    raise SystemExit(main())
