#!/usr/bin/env python3
"""Simulador de flota alrededor de Bogotá que envía posiciones por la API.

- Identificadores estables: BOG-001, BOG-002, ...
- Recorridos reproducibles (--seed) en circuitos elípticos por la ciudad.
- Cada nueva posición lleva un event_id nuevo; si el envío falla y se
  reintenta, se reutiliza el mismo event_id (la API/consumidor deduplican).
- captured_at es la hora actual en UTC.
- Ctrl+C (SIGINT) o SIGTERM detienen la simulación al terminar el ciclo.

Solo usa la biblioteca estándar.

Ejemplo:
    python scripts/simulate.py --url http://localhost:8000 --vehicles 8 --interval 2 --duration 300
"""

from __future__ import annotations

import argparse
import json
import math
import random
import signal
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

# Puntos de referencia de Bogotá (lat, lon).
ANCHORS = [
    (4.6097, -74.0817),  # Centro
    (4.6486, -74.0628),  # Chapinero
    (4.6764, -74.0482),  # Zona T / Andino
    (4.7020, -74.0420),  # Usaquén
    (4.6280, -74.0640),  # Teusaquillo
    (4.6680, -74.1050),  # Salitre
    (4.5980, -74.1300),  # Kennedy
    (4.7110, -74.0720),  # Suba / Niza
]

EARTH_RADIUS_M = 6_371_000


@dataclass
class SimVehicle:
    vehicle_id: str
    center_lat: float
    center_lon: float
    radius_lat: float      # grados
    radius_lon: float      # grados
    angle: float           # radianes
    base_speed_kmh: float
    direction: int         # 1 horario, -1 antihorario
    stopped_ticks: int = 0
    lat: float = 0.0
    lon: float = 0.0

    def position_at(self, angle: float) -> tuple[float, float]:
        return (self.center_lat + self.radius_lat * math.sin(angle),
                self.center_lon + self.radius_lon * math.cos(angle))


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def build_fleet(count: int, rng: random.Random) -> list[SimVehicle]:
    fleet = []
    for i in range(count):
        anchor_lat, anchor_lon = ANCHORS[i % len(ANCHORS)]
        v = SimVehicle(
            vehicle_id=f"BOG-{i + 1:03d}",
            center_lat=anchor_lat + rng.uniform(-0.004, 0.004),
            center_lon=anchor_lon + rng.uniform(-0.004, 0.004),
            radius_lat=rng.uniform(0.006, 0.015),
            radius_lon=rng.uniform(0.006, 0.015),
            angle=rng.uniform(0, 2 * math.pi),
            base_speed_kmh=rng.uniform(18, 55),
            direction=rng.choice((1, -1)),
        )
        v.lat, v.lon = v.position_at(v.angle)
        fleet.append(v)
    return fleet


def advance(v: SimVehicle, dt_s: float, rng: random.Random) -> float:
    """Mueve el vehículo y devuelve su velocidad en km/h."""
    if v.stopped_ticks > 0:
        v.stopped_ticks -= 1
        return 0.0
    if rng.random() < 0.04:  # semáforo o parada
        v.stopped_ticks = rng.randint(1, 4)
        return 0.0

    speed_kmh = max(5.0, v.base_speed_kmh + rng.uniform(-8, 8))
    distance_m = speed_kmh / 3.6 * dt_s
    # Avanza por la elipse hasta cubrir aproximadamente esa distancia.
    step = 0.002
    travelled = 0.0
    prev = (v.lat, v.lon)
    while travelled < distance_m:
        v.angle += v.direction * step
        cur = v.position_at(v.angle)
        travelled += haversine_m(prev, cur)
        prev = cur
    v.lat, v.lon = prev
    return speed_kmh


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def post_position(url: str, event: dict, retries: int, timeout_s: float) -> tuple[bool, int]:
    """Envía el evento; los reintentos reutilizan el mismo event_id."""
    body = json.dumps(event).encode()
    for attempt in range(retries + 1):
        req = urllib.request.Request(f"{url}/v1/positions", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                if resp.status == 202:
                    return True, attempt
        except urllib.error.HTTPError as exc:
            if exc.code == 422:  # inválido: reintentar no sirve
                print(f"  {event['vehicle_id']}: rechazado 422 {exc.read().decode()[:200]}",
                      file=sys.stderr)
                return False, attempt
            print(f"  {event['vehicle_id']}: HTTP {exc.code}, reintento {attempt + 1}/{retries}"
                  f" con event_id={event['event_id']}", file=sys.stderr)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            print(f"  {event['vehicle_id']}: {exc}, reintento {attempt + 1}/{retries}"
                  f" con event_id={event['event_id']}", file=sys.stderr)
        if attempt < retries:
            time.sleep(min(0.5 * 2 ** attempt, 5))
    return False, retries


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="http://localhost:8000", help="URL base de la API")
    p.add_argument("--vehicles", type=int, default=8, help="cantidad de vehículos")
    p.add_argument("--interval", type=float, default=2.0, help="segundos entre posiciones por vehículo")
    p.add_argument("--duration", type=float, default=0, help="segundos de simulación (0 = sin límite)")
    p.add_argument("--seed", type=int, default=42, help="semilla para recorridos reproducibles")
    p.add_argument("--retries", type=int, default=3, help="reintentos por posición")
    p.add_argument("--timeout", type=float, default=10.0, help="timeout HTTP en segundos")
    args = p.parse_args(argv)
    if args.vehicles < 1 or args.interval <= 0:
        p.error("--vehicles debe ser >= 1 y --interval > 0")

    stop = False

    def request_stop(signum, _frame):
        nonlocal stop
        if not stop:
            print(f"\nSeñal {signum} recibida: terminando después de este ciclo…", file=sys.stderr)
        stop = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    rng = random.Random(args.seed)
    fleet = build_fleet(args.vehicles, rng)
    url = args.url.rstrip("/")
    print(f"Simulando {len(fleet)} vehículos contra {url} cada {args.interval}s"
          + (f" durante {args.duration}s" if args.duration else " (Ctrl+C para detener)"))

    sent = failed = retried = 0
    started = time.monotonic()
    tick = 0
    while not stop:
        tick_start = time.monotonic()
        for v in fleet:
            if stop:
                break
            speed = advance(v, args.interval, rng) if tick > 0 else 0.0
            event = {
                "schema_version": 1,
                "event_id": str(uuid.uuid4()),  # nuevo por cada posición nueva
                "vehicle_id": v.vehicle_id,
                "captured_at": utc_now_iso(),
                "lat": round(v.lat, 6),
                "lon": round(v.lon, 6),
                "speed_kmh": round(speed, 1),
            }
            ok, attempts = post_position(url, event, args.retries, args.timeout)
            sent += ok
            failed += not ok
            retried += attempts
        tick += 1
        print(f"ciclo {tick}: enviadas={sent} fallidas={failed} reintentos={retried}", flush=True)
        if args.duration and time.monotonic() - started >= args.duration:
            break
        # Espera hasta el próximo ciclo, revisando la señal de parada.
        remaining = args.interval - (time.monotonic() - tick_start)
        while remaining > 0 and not stop:
            time.sleep(min(remaining, 0.2))
            remaining = args.interval - (time.monotonic() - tick_start)

    print(f"Fin: enviadas={sent} fallidas={failed} reintentos={retried}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
