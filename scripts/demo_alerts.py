#!/usr/bin/env python3
"""Escenarios de DEMOSTRACIÓN para las alertas de detención en zonas.

Publica posiciones ficticias por la API existente (POST /v1/positions) con
fechas relativas al momento de ejecución. Los vehículos se llaman
DEMO-<etiqueta>-<escenario>; la etiqueta (por defecto la hora UTC HHMM)
evita que una ejecución anterior interfiera con la nueva.

Escenarios (umbral por defecto: más de 20 min, muestras cada 30 s):
  ZONA25    detenido 25 min dentro de DEMO-Z01           -> alerta
  FUERA     detenido 30 min fuera de las zonas           -> sin alerta
  MOVIL     en movimiento dentro de DEMO-Z01             -> sin alerta
  HUECO     detenido en DEMO-Z01 con 6 min sin muestras  -> sin alerta
            (el tramo se reinicia tras la interrupción)
  SINDATOS  última muestra hace 6 min                     -> evidencia insuficiente
  ATRASADO  detenido 30 min en DEMO-Z04 -> alerta; luego llega un evento
            atrasado de movimiento de hace 12 min        -> la alerta se retira

Después, salvo con --once, sigue enviando una muestra actual cada
--live-interval segundos para ZONA25, FUERA, MOVIL, HUECO y ATRASADO, así
sus últimas muestras no se desactualizan. Mientras sigue en vivo, HUECO y
ATRASADO acumulan un tramo nuevo y generan alerta cuando superan el umbral
(unos 12 y 8 minutos después): es el comportamiento esperado.

Cada posición tiene un event_id nuevo; los reintentos reutilizan el mismo.
Solo usa la biblioteca estándar.

Ejemplo:
    python scripts/demo_alerts.py --url http://localhost:8000
"""

from __future__ import annotations

import argparse
import json
import math
import random
import signal
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

from simulate import post_position

STEP_S = 30
M_PER_DEG_LAT = 111_195.0
# Fuera de todas las zonas de demostración (Parque Simón Bolívar, aprox.).
OUTSIDE = (4.6584, -74.0937)


def offset(lat: float, lon: float, north_m: float, east_m: float) -> tuple[float, float]:
    return (lat + north_m / M_PER_DEG_LAT,
            lon + east_m / (M_PER_DEG_LAT * math.cos(math.radians(lat))))


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def get_json(url: str, timeout: float = 10) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class Scenario:
    def __init__(self, vehicle_id: str, expected: str, rng: random.Random):
        self.vehicle_id = vehicle_id
        self.expected = expected
        self.rng = rng
        self.events: list[dict] = []

    def stopped(self, start: datetime, end: datetime, at: tuple[float, float]) -> None:
        t = start
        while t <= end:
            self.add(t, at, speed=round(self.rng.uniform(0, 1.5), 1), jitter_m=4)
            t += timedelta(seconds=STEP_S)

    def add(self, t: datetime, at: tuple[float, float], speed: float, jitter_m: float = 0) -> dict:
        lat, lon = offset(*at, self.rng.uniform(-jitter_m, jitter_m),
                          self.rng.uniform(-jitter_m, jitter_m))
        event = {
            "schema_version": 1,
            "event_id": str(uuid.uuid4()),
            "vehicle_id": self.vehicle_id,
            "captured_at": iso(t),
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "speed_kmh": speed,
        }
        self.events.append(event)
        return event


def find_zone(zones: list[dict], zone_id: str) -> tuple[float, float]:
    for z in zones:
        if z["zone_id"] == zone_id:
            return z["center_lat"], z["center_lon"]
    raise SystemExit(f"No está activa la zona de demostración {zone_id}. "
                     "¿Se aplicó la migración 003_critical_zones?")


def vehicle_state(alerts: dict, vehicle_id: str) -> str:
    if any(a["vehicle_id"] == vehicle_id for a in alerts["alerts"]):
        zones = ",".join(a["zone_id"] for a in alerts["alerts"] if a["vehicle_id"] == vehicle_id)
        return f"alerta ({zones})"
    for i in alerts["insufficient_evidence"]:
        if i["vehicle_id"] == vehicle_id:
            return f"evidencia insuficiente ({i['reason']})"
    return "sin alerta"


def wait_for_state(url: str, vehicle_id: str, predicate, timeout_s: float) -> str:
    deadline = time.monotonic() + timeout_s
    state = "?"
    while time.monotonic() < deadline:
        try:
            state = vehicle_state(get_json(f"{url}/v1/alerts"), vehicle_id)
            if predicate(state):
                return state
        except Exception as exc:  # noqa: BLE001 - la API puede estar degradada
            state = f"error consultando alertas: {exc}"
        time.sleep(2)
    return state


def publish(url: str, events: list[dict], retries: int, timeout: float) -> int:
    failed = 0
    for event in events:
        ok, _ = post_position(url, event, retries, timeout)
        failed += not ok
    return failed


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="http://localhost:8000", help="URL base de la API")
    p.add_argument("--tag", default=None, help="etiqueta de los vehículos (por defecto HHMM UTC)")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--once", action="store_true", help="publica los escenarios y termina")
    p.add_argument("--live-interval", type=float, default=30.0)
    p.add_argument("--live-duration", type=float, default=0, help="segundos en vivo (0 = hasta Ctrl+C)")
    p.add_argument("--wait", type=float, default=90.0, help="espera máxima por cada comprobación")
    p.add_argument("--retries", type=int, default=3)
    p.add_argument("--timeout", type=float, default=10.0)
    args = p.parse_args(argv)

    url = args.url.rstrip("/")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    tag = args.tag or now.strftime("%H%M")
    rng = random.Random(args.seed)
    zones = get_json(f"{url}/v1/zones")["zones"]
    z01 = find_zone(zones, "DEMO-Z01")
    z04 = find_zone(zones, "DEMO-Z04")
    ago = lambda minutes: now - timedelta(minutes=minutes)  # noqa: E731

    print("=== ESCENARIOS DE DEMOSTRACIÓN (datos ficticios) ===")
    print(f"API {url} · etiqueta {tag} · hora de referencia {iso(now)}")

    def vid(name: str) -> str:
        return f"DEMO-{tag}-{name}"

    zona25 = Scenario(vid("ZONA25"), "alerta en DEMO-Z01", rng)
    zona25_at = offset(*z01, 150, 0)
    zona25.stopped(ago(25), now, zona25_at)

    fuera = Scenario(vid("FUERA"), "sin alerta (fuera de zonas)", rng)
    fuera.stopped(ago(30), now, OUTSIDE)

    movil = Scenario(vid("MOVIL"), "sin alerta (en movimiento)", rng)
    angle = 0.0
    t = ago(25)
    while t <= now:
        speed = round(rng.uniform(15, 25), 1)
        angle += speed / 3.6 * STEP_S / 200.0          # recorre un círculo de 200 m
        movil.add(t, offset(*z01, 200 * math.sin(angle), 200 * math.cos(angle)), speed)
        t += timedelta(seconds=STEP_S)

    hueco = Scenario(vid("HUECO"), "sin alerta (interrupción de 6 min)", rng)
    hueco_at = offset(*z01, -150, 0)
    hueco.stopped(ago(40), ago(14), hueco_at)
    hueco.stopped(ago(8), now, hueco_at)

    sin_datos = Scenario(vid("SINDATOS"), "evidencia insuficiente (última muestra hace 6 min)", rng)
    sin_datos.stopped(ago(45), ago(6), offset(*z01, 0, 150))

    atrasado = Scenario(vid("ATRASADO"), "alerta en DEMO-Z04, retirada por evento atrasado", rng)
    atrasado.stopped(ago(30), now, z04)

    scenarios = [zona25, fuera, movil, hueco, sin_datos, atrasado]
    failed = 0
    for s in scenarios:
        failed += publish(url, s.events, args.retries, args.timeout)
        print(f"  {s.vehicle_id}: {len(s.events)} muestras · esperado: {s.expected}")
    if failed:
        print(f"ADVERTENCIA: {failed} posiciones no se pudieron publicar", file=sys.stderr)

    # Evento atrasado: primero se espera la alerta y luego se publica un
    # movimiento de hace 12 minutos que la invalida.
    print(f"Esperando la alerta de {atrasado.vehicle_id}…")
    state = wait_for_state(url, atrasado.vehicle_id, lambda s: s.startswith("alerta"), args.wait)
    print(f"  antes del evento atrasado: {state}")
    late = atrasado.add(ago(12) + timedelta(seconds=15), z04, speed=30.0)
    failed += publish(url, [late], args.retries, args.timeout)
    print(f"  publicado evento atrasado {late['event_id']} ({late['captured_at']}, 30 km/h)")
    state = wait_for_state(url, atrasado.vehicle_id, lambda s: s == "sin alerta", args.wait)
    print(f"  después del evento atrasado: {state}")

    print("Estado según GET /v1/alerts:")
    alerts = get_json(f"{url}/v1/alerts")
    for s in scenarios:
        print(f"  {s.vehicle_id:<22} {vehicle_state(alerts, s.vehicle_id):<40} esperado: {s.expected}")
    if not alerts["complete"]:
        print(f"  Evaluación PARCIAL: {alerts['incomplete_reasons']}")

    if args.once:
        print("Fin (--once). Sin muestras nuevas, las alertas pasarán a evidencia "
              "insuficiente cuando la última muestra supere la antigüedad máxima.")
        return 0 if failed == 0 else 1

    stop = False

    def request_stop(signum, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    live = [(zona25, zona25_at, 0.0), (fuera, OUTSIDE, 0.0), (hueco, hueco_at, 0.0),
            (atrasado, z04, 0.0)]
    print(f"En vivo: una muestra cada {args.live_interval:g} s (Ctrl+C para terminar)")
    started = time.monotonic()
    while not stop:
        t = datetime.now(timezone.utc)
        batch = [s.add(t, at, speed=0.0, jitter_m=4) for s, at, _ in live]
        speed = round(rng.uniform(15, 25), 1)
        angle += speed / 3.6 * args.live_interval / 200.0
        batch.append(movil.add(t, offset(*z01, 200 * math.sin(angle), 200 * math.cos(angle)), speed))
        failed += publish(url, batch, args.retries, args.timeout)
        print(f"  {iso(t)}: {len(batch)} muestras en vivo", flush=True)
        if args.live_duration and time.monotonic() - started >= args.live_duration:
            break
        deadline = time.monotonic() + args.live_interval
        while not stop and time.monotonic() < deadline:
            time.sleep(0.2)
    print("Fin de la demostración.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
