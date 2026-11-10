"""AT-4: el consumidor cae después del COMMIT en la base y antes del commit de offset.

Controla el contenedor del consumidor con `docker compose`, así que se
ejecuta desde la raíz del repositorio y se omite si no hay CLI de Docker.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timezone

import pytest

from conftest import (
    ROOT,
    consumer_lag,
    current_position,
    dedup_count,
    history_rows,
    make_event,
    wait_for_consumer_caught_up,
)

pytestmark = [
    pytest.mark.crash,
    pytest.mark.skipif(shutil.which("docker") is None, reason="requiere docker compose"),
]

FAIL_EXIT_CODE = 3


def compose(*args: str, check: bool = True, timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "compose", *args], cwd=ROOT, check=check,
                          capture_output=True, text=True, timeout=timeout)


def test_at4_caida_entre_commit_de_bd_y_commit_de_offset(api, db, vehicle_id):
    wait_for_consumer_caught_up()
    compose("stop", "consumer")
    try:
        event = make_event(vehicle_id, datetime(2026, 11, 5, 11, 0, tzinfo=timezone.utc))
        assert api.post("/v1/positions", json=event).status_code == 202
        assert consumer_lag() >= 1

        # Consumidor temporal que guarda en la BD y sale sin confirmar el offset.
        crashed = compose("run", "--rm", "--no-deps", "-e", "FAIL_AFTER_DB_COMMIT=1",
                          "consumer", check=False)
        assert crashed.returncode == FAIL_EXIT_CODE, crashed.stdout + crashed.stderr

        # Los datos quedaron, pero Kafka sigue pensando que faltan mensajes.
        assert len(history_rows(db, vehicle_id)) == 1
        before = current_position(db, vehicle_id)
        assert consumer_lag() >= 1
    finally:
        compose("start", "consumer")

    # El consumidor normal relee el mensaje, la deduplicación lo descarta
    # y por fin confirma el offset.
    wait_for_consumer_caught_up(timeout=60)
    assert len(history_rows(db, vehicle_id)) == 1
    assert dedup_count(db, event["event_id"]) == 1
    assert current_position(db, vehicle_id) == before
