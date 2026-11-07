"""Generador del conjunto de carga (loadtest/prepare.py), sin dobles."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from fleet_contracts import PositionEvent, check_not_in_future
from prepare import BLOCK, build, main

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)


def requests(total=200, vehicles=30, seed=7, run="t1"):
    return build(run, vehicles, total, seed, NOW)["requests"]


def test_cuotas_exactas_por_bloque():
    reqs = requests(total=200)
    for start in range(0, 200, BLOCK):
        block = Counter(r["kind"] for r in reqs[start:start + BLOCK])
        assert block == {"new": 17, "dup": 2, "invalid": 1}
    assert Counter(r["kind"] for r in reqs) == {"new": 170, "dup": 20, "invalid": 10}


def test_bloque_incompleto_por_mayor_resto():
    counts = build("t2", 10, 50, 1, NOW)["counts"]
    assert counts == {"new": 43, "dup": 5, "invalid": 2}
    assert sum(counts.values()) == 50


def test_reproducible_con_la_misma_semilla_y_distinto_con_otra():
    assert requests(seed=7) == requests(seed=7)
    assert requests(seed=7) != requests(seed=8)


def test_reenvios_exactos_de_un_evento_previo():
    reqs = requests(total=400)
    for r in (x for x in reqs if x["kind"] == "dup"):
        src = reqs[r["source_seq"]]
        assert src["kind"] == "new"
        assert src["seq"] < r["seq"]
        assert (r["event_id"], r["body"]) == (src["event_id"], src["body"])
        if r["seq"] >= BLOCK:
            assert src["seq"] < (r["seq"] // BLOCK) * BLOCK      # de un bloque anterior


def test_eventos_nuevos_validos_unicos_y_vehiculos_estables():
    reqs = requests(total=400, vehicles=30, run="t3")
    new = [r for r in reqs if r["kind"] == "new"]
    assert len({r["event_id"] for r in new}) == len(new)
    for r in new:
        event = PositionEvent.model_validate(json.loads(r["body"]))
        check_not_in_future(event, NOW)
        assert event.vehicle_id.startswith("LT-t3-") and 4.5 < event.lat < 4.8 and -74.2 < event.lon < -73.9
        assert r["body"] == json.dumps(json.loads(r["body"]), sort_keys=True, separators=(",", ":"))
    per_vehicle = Counter(r["vehicle_id"] for r in new)
    assert len(per_vehicle) == 30 and max(per_vehicle.values()) - min(per_vehicle.values()) <= 1
    # Fechas crecientes por vehículo, en UTC con "Z".
    by_vehicle: dict[str, list[str]] = {}
    for r in new:
        by_vehicle.setdefault(r["vehicle_id"], []).append(r["captured_at"])
    for times in by_vehicle.values():
        assert times == sorted(times) and all(t.endswith("Z") for t in times)


def test_invalidos_fallan_por_la_regla_de_latitud():
    reqs = requests(total=400)
    invalid = [r for r in reqs if r["kind"] == "invalid"]
    ids = {r["event_id"] for r in reqs if r["kind"] != "invalid"}
    for r in invalid:
        body = json.loads(r["body"])
        assert body["lat"] == 95.0 and r["event_id"] not in ids
        with pytest.raises(ValidationError) as exc:
            PositionEvent.model_validate(body)
        assert [e["loc"] for e in exc.value.errors()] == [("lat",)]   # única regla incumplida


def test_identidades_unicas_esperadas_no_cuentan_reenvios():
    built = build("t4", 10, 100, 3, NOW)
    reqs = built["requests"]
    assert len({r["event_id"] for r in reqs if r["kind"] != "invalid"}) == built["counts"]["new"]


@pytest.mark.parametrize("run_id", ["", "con espacio", "x" * 25, "-empieza"])
def test_run_id_invalido(run_id):
    with pytest.raises(ValueError):
        build(run_id, 1, 10, 1, NOW)


def test_cli_escribe_meta_y_dataset(tmp_path, capsys):
    assert main(["--run-id", "cli-1", "--vehicles", "5", "--rate", "4", "--duration", "5",
                 "--seed", "2", "--out", str(tmp_path)]) == 0
    meta = json.loads((tmp_path / "cli-1" / "meta.json").read_text())
    data = json.loads((tmp_path / "cli-1" / "dataset.json").read_text())
    assert meta["planned_requests"] == len(data) == 20
    assert meta["planned_counts"] == {"new": 17, "dup": 2, "invalid": 1}
    assert meta["vehicle_ids"] == [f"LT-cli-1-{i:04d}" for i in range(1, 6)]
    assert main(["--run-id", "cli-2", "--vehicles", "5", "--rate", "2", "--duration", "3",
                 "--mode", "batch", "--batch-size", "10", "--out", str(tmp_path)]) == 0
    assert json.loads((tmp_path / "cli-2" / "meta.json").read_text())["planned_requests"] == 60
