"""Reconciliación (loadtest/reconcile_lib.py): lógica pura con instantáneas de BD
construidas en la prueba (dobles de la BD; no se conecta a TimescaleDB)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fleet_contracts import PositionEvent
from prepare import build
from reconcile_lib import (
    DbSnapshot, Observation, build_manifest, evaluate, identities, load_points, parse_time, percentiles,
    sent_counts,
)

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
T = NOW + timedelta(minutes=1)


def scenario(total=40, vehicles=4):
    built = build("rc", vehicles, total, 5, NOW)
    reqs = built["requests"]
    vehicles_ids = sorted({r["vehicle_id"] for r in reqs})
    return reqs, vehicles_ids


def observe(reqs, outcome_for=None):
    """Observaciones como las de k6: válidos confirmados, inválidos rechazados."""
    out = []
    for r in reqs:
        outcome = (outcome_for or {}).get(r["seq"])
        if outcome is None:
            outcome = "expected_rejection" if r["kind"] == "invalid" else "confirmed"
        if outcome == "not_sent":
            continue
        status = {"confirmed": "202", "expected_rejection": "422", "ambiguous": "timeout"}.get(outcome, "500")
        out.append(Observation(r["seq"], outcome, status, T + timedelta(milliseconds=r["seq"]), "k6-points-x.json"))
    return out


def db_from(reqs, *, skip=(), extra_rows=None, mutate=None):
    """Instantánea de una BD correcta: cada identidad válida persistida una vez."""
    ingested, history, seen = {}, {}, set()
    for r in reqs:
        if r["kind"] == "invalid" or r["event_id"] in seen or r["event_id"] in skip:
            continue
        seen.add(r["event_id"])
        e = json.loads(r["body"])
        row = {"event_id": e["event_id"], "vehicle_id": e["vehicle_id"], "captured_at": parse_time(e["captured_at"]),
               "lat": e["lat"], "lon": e["lon"], "speed_kmh": float(e["speed_kmh"]),
               "ingested_at": T + timedelta(seconds=1)}
        if mutate:
            mutate(row)
        history[e["event_id"]] = [row]
        ingested[e["event_id"]] = {"vehicle_id": e["vehicle_id"],
                                   "payload_hash": PositionEvent.model_validate(e).payload_hash()}
    for row in extra_rows or []:
        history.setdefault(row["event_id"], []).append(row)
    current = {}
    for rows in history.values():
        for row in rows:
            best = current.get(row["vehicle_id"])
            if best is None or (row["captured_at"], row["event_id"]) > (best["captured_at"], best["event_id"]):
                current[row["vehicle_id"]] = {"event_id": row["event_id"], "captured_at": row["captured_at"]}
    return DbSnapshot(ingested, history, current, {})


def run(reqs, vehicles, obs, db):
    manifest = build_manifest(reqs, obs)
    ids = identities(manifest)
    db.expected_hashes = {i.event_id: PositionEvent.model_validate(i.expected).payload_hash()
                          for i in ids.values() if i.kind == "valid"}
    return evaluate(ids, db, vehicles), manifest, ids


def types(result):
    return sorted({d["type"] for d in result["discrepancies"]})


def test_ejecucion_correcta_sin_discrepancias():
    reqs, vehicles = scenario()
    result, manifest, ids = run(reqs, vehicles, observe(reqs), db_from(reqs))
    assert result["discrepancies"] == []
    new = sum(r["kind"] == "new" for r in reqs)
    assert result["counts"]["confirmed"] == result["counts"]["persisted_ok"] == new   # reenvíos no suman
    assert result["counts"]["invalid"] == sum(r["kind"] == "invalid" for r in reqs)
    assert result["counts"]["vehicles_checked"] == len(vehicles)
    executed = sent_counts(manifest)
    assert executed["dup"]["confirmed"] == sum(r["kind"] == "dup" for r in reqs)


def test_falta_un_confirmado_y_el_lag_cero_no_lo_oculta():
    reqs, vehicles = scenario()
    lost = next(r["event_id"] for r in reqs if r["kind"] == "new")
    result, _, _ = run(reqs, vehicles, observe(reqs), db_from(reqs, skip={lost}))
    assert {"missing_history", "missing_ingested"} <= set(types(result))
    assert all(d["event_id"] == lost for d in result["discrepancies"]
               if d["type"] in ("missing_history", "missing_ingested"))


def test_fila_historica_duplicada():
    reqs, vehicles = scenario()
    first = next(r for r in reqs if r["kind"] == "new")
    e = json.loads(first["body"])
    dup_row = {"event_id": e["event_id"], "vehicle_id": e["vehicle_id"],
               "captured_at": parse_time(e["captured_at"]) + timedelta(seconds=1),
               "lat": e["lat"], "lon": e["lon"], "speed_kmh": e["speed_kmh"], "ingested_at": T}
    result, _, _ = run(reqs, vehicles, observe(reqs), db_from(reqs, extra_rows=[dup_row]))
    assert "duplicate_history" in types(result)


def test_contenido_distinto_y_tolerancia_de_float32():
    reqs, vehicles = scenario()
    ok, _, _ = run(reqs, vehicles, observe(reqs),
                   db_from(reqs, mutate=lambda r: r.update(speed_kmh=r["speed_kmh"] + 1e-5)))
    assert ok["discrepancies"] == []                  # redondeo de la columna real
    bad, _, _ = run(reqs, vehicles, observe(reqs),
                    db_from(reqs, mutate=lambda r: r.update(lat=r["lat"] + 0.001)))
    assert "content_mismatch" in types(bad)


def test_hash_del_contenido_en_ingested_events():
    reqs, vehicles = scenario()
    db = db_from(reqs)
    some = next(iter(db.ingested))
    db.ingested[some]["payload_hash"] = b"\x00" * 32
    result, _, _ = run(reqs, vehicles, observe(reqs), db)
    assert types(result) == ["payload_hash_mismatch"]


def test_invalido_persistido_y_fila_desconocida():
    reqs, vehicles = scenario()
    inv = next(r for r in reqs if r["kind"] == "invalid")
    db = db_from(reqs)
    db.ingested[inv["event_id"]] = {"vehicle_id": inv["vehicle_id"], "payload_hash": b""}
    db.history["00000000-0000-4000-8000-000000000000"] = [{
        "event_id": "00000000-0000-4000-8000-000000000000", "vehicle_id": vehicles[0],
        "captured_at": NOW, "lat": 4.6, "lon": -74.1, "speed_kmh": 1.0, "ingested_at": T}]
    result, _, _ = run(reqs, vehicles, observe(reqs), db)
    assert {"invalid_persisted", "persisted_unknown"} <= set(types(result))


def test_ambiguos_se_informan_aparte_persistidos_o_no():
    reqs, vehicles = scenario()
    news = [r for r in reqs if r["kind"] == "new"]
    dup_sources = {r["source_seq"] for r in reqs if r["kind"] == "dup"}
    ambiguous = [r for r in news if r["seq"] not in dup_sources][:2]
    obs = observe(reqs, {r["seq"]: "ambiguous" for r in ambiguous})
    db = db_from(reqs, skip={ambiguous[1]["event_id"]})   # uno llegó a publicarse, el otro no
    result, _, ids = run(reqs, vehicles, obs, db)
    assert types(result) == []
    assert result["counts"]["ambiguous"] == 2 and result["counts"]["ambiguous_persisted"] == 1
    assert {a["event_id"]: a["persisted"] for a in result["ambiguous"]} == {
        ambiguous[0]["event_id"]: True, ambiguous[1]["event_id"]: False}


def test_reenvio_confirmado_cubre_un_original_ambiguo():
    reqs, vehicles = scenario()
    dup = next(r for r in reqs if r["kind"] == "dup")
    obs = observe(reqs, {dup["source_seq"]: "ambiguous"})
    result, _, ids = run(reqs, vehicles, obs, db_from(reqs))
    assert ids[dup["event_id"]].status == "confirmed"
    assert result["discrepancies"] == []


def test_posicion_actual_con_desempate_por_event_id():
    reqs, vehicles = scenario()
    db = db_from(reqs)
    v = vehicles[0]
    rows = [r for rows in db.history.values() for r in rows if r["vehicle_id"] == v]
    best = max(rows, key=lambda r: (r["captured_at"], r["event_id"]))
    tie = dict(best, event_id="ffffffff-ffff-4fff-bfff-ffffffffffff")   # mismo instante, id mayor
    db.history[tie["event_id"]] = [tie]
    db.current[v] = {"event_id": best["event_id"], "captured_at": best["captured_at"]}
    result, _, _ = run(reqs, vehicles, observe(reqs), db)
    assert "current_mismatch" in types(result)        # debió ganar el event_id mayor
    assert "persisted_unknown" in types(result)


def test_persistido_sin_envio():
    reqs, vehicles = scenario()
    dup_sources = {r["source_seq"] for r in reqs if r["kind"] == "dup"}
    unsent = next(r for r in reqs if r["kind"] == "new" and r["seq"] not in dup_sources)
    result, _, _ = run(reqs, vehicles, observe(reqs, {unsent["seq"]: "not_sent"}), db_from(reqs))
    assert types(result) == ["persisted_without_send"]


def test_lectura_de_la_salida_json_de_k6(tmp_path):
    lines = [
        {"type": "Metric", "data": {"name": "lt_result"}, "metric": "lt_result"},
        {"type": "Point", "metric": "http_req_duration", "data": {"time": "2026-11-07T12:00:00Z", "value": 3, "tags": {}}},
        {"type": "Point", "metric": "lt_result", "data": {"time": "2026-11-07T12:00:01.5-05:00", "value": 1,
                                                          "tags": {"seq": "3", "outcome": "confirmed", "status": "202", "kind": "new"}}},
    ]
    f = tmp_path / "k6-points-ingest.json"
    f.write_text("\n".join(json.dumps(x) for x in lines))
    [obs] = load_points([f])
    assert (obs.seq, obs.outcome, obs.status) == (3, "confirmed", "202")
    assert obs.time == datetime(2026, 11, 7, 17, 0, 1, 500000, tzinfo=timezone.utc)


def test_percentiles():
    assert percentiles([]) == {"p50": None, "p95": None, "p99": None}
    assert percentiles(list(range(1, 101))) == {"p50": 50, "p95": 95, "p99": 99}
