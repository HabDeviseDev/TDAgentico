"""Verifica la configuración de Compose que se despliega en AWS.

Renderiza docker-compose.yml + infra/deploy/compose.aws.yml (todos los
perfiles) con valores ficticios y comprueba:

- solo Caddy publica puertos, y solo 80 y 443;
- Kafka no conserva el listener EXTERNAL;
- ninguna credencial de ejemplo ("fleet:fleet", POSTGRES_PASSWORD=fleet)
  sobrevive en la configuración de AWS;
- los volúmenes de datos apuntan al disco persistente.

Uso: python infra/scripts/check_compose_ports.py
(requiere docker compose; no necesita credenciales ni contenedores).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALLOWED = {"caddy": {80, 443}}
DUMMY_PASSWORD = "ValidacionFicticia_0123456789"


def render() -> dict:
    env = dict(os.environ)
    env.update(
        POSTGRES_PASSWORD=DUMMY_PASSWORD,
        ANTHROPIC_API_KEY="clave-ficticia",
        SITE_ADDRESS="flota.example.com",
        FLEET_DATA_DIR="/data",
    )
    cmd = [
        "docker", "compose",
        "--project-directory", str(ROOT),
        "-f", str(ROOT / "docker-compose.yml"),
        "-f", str(ROOT / "infra/deploy/compose.aws.yml"),
        "--profile", "*",
        "config", "--format", "json",
    ]
    out = subprocess.run(cmd, env=env, check=True, capture_output=True, text=True)
    return json.loads(out.stdout)


def check(config: dict) -> list[str]:
    errors: list[str] = []
    services = config["services"]

    for name, svc in sorted(services.items()):
        published = {int(p["published"]) for p in svc.get("ports", []) if p.get("published")}
        targets = {int(p["target"]) for p in svc.get("ports", [])}
        allowed = ALLOWED.get(name, set())
        if published - allowed or (targets and name not in ALLOWED):
            errors.append(f"{name}: publica puertos no permitidos {sorted(published or targets)}")
        print(f"  {name:12s} puertos={sorted(published) or '-'}")

    for name in ALLOWED:
        if name not in services:
            errors.append(f"falta el servicio {name}")

    kafka_env = services["kafka"]["environment"]
    for key in ("KAFKA_LISTENERS", "KAFKA_ADVERTISED_LISTENERS", "KAFKA_LISTENER_SECURITY_PROTOCOL_MAP"):
        if "EXTERNAL" in str(kafka_env.get(key, "")):
            errors.append(f"kafka: {key} conserva el listener EXTERNAL")

    rendered = json.dumps(services)
    if "fleet:fleet@" in rendered:
        errors.append("queda una URL de conexión con la contraseña local fleet:fleet")
    for name, svc in services.items():
        envs = svc.get("environment") or {}
        for key in ("POSTGRES_PASSWORD", "PGPASSWORD"):
            if envs.get(key) == "fleet":
                errors.append(f"{name}: {key} conserva el valor local 'fleet'")

    for vol in ("kafka-data", "tsdb-data"):
        device = (config.get("volumes", {}).get(vol, {}).get("driver_opts") or {}).get("device", "")
        if not device.startswith("/data/"):
            errors.append(f"volumen {vol} no está en /data (device={device!r})")

    return errors


def main() -> int:
    try:
        config = render()
    except subprocess.CalledProcessError as exc:
        print(exc.stderr, file=sys.stderr)
        print("ERROR docker compose config falló", file=sys.stderr)
        return 1
    print("Servicios en la configuración de AWS:")
    errors = check(config)
    if errors:
        print("\nFALLA:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("\nOK: solo caddy publica 80/443; sin listener EXTERNAL; sin credenciales locales; datos en /data")
    return 0


if __name__ == "__main__":
    sys.exit(main())
