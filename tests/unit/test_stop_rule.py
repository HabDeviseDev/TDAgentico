"""Regla de detención en zonas: casos de borde (dominio puro, reloj inyectado)."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from app.alerts.rules import (
    EARTH_RADIUS_M, REASON_MAX_ROWS, REASON_MAX_VEHICLES, REASON_STALE, Sample,
    StopRuleParams, Zone, evaluate_fleet, evaluate_vehicle, haversine_m,
)

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
P = StopRuleParams()  # 3 km/h, 50 m, 120 s, 120 s, 20 min, 2 h
CENTER = (4.6230, -74.1150)
ZONE = Zone("Z1", "Zona 1", *CENTER, radius_m=400)
_ids = iter(range(1, 10**9))


def eid(n: int | None = None) -> str:
    """event_id con formato UUID; el orden numérico coincide con el de texto."""
    return f"00000000-0000-0000-0000-{(n if n is not None else next(_ids)):012x}"


def north(lat: float, lon: float, meters: float) -> tuple[float, float]:
    """Punto `meters` al norte (sobre el meridiano la distancia es exacta)."""
    return lat + math.degrees(meters / EARTH_RADIUS_M), lon


def stopped(start: datetime, end: datetime, step_s: int = 60, at=CENTER,
            vehicle="V1", speed=0.0) -> list[Sample]:
    out, t = [], start
    while t <= end:
        out.append(Sample(vehicle, eid(), t, at[0], at[1], speed))
        t += timedelta(seconds=step_s)
    return out


def alerts_for(samples, zones=(ZONE,), now=NOW, window_start=None, p=P):
    return evaluate_vehicle("V1", samples, list(zones), p, now, window_start).alerts


def minutes(m: float) -> timedelta:
    return timedelta(minutes=m)


# -- duración ---------------------------------------------------------------

def test_mas_de_20_minutos_genera_alerta_con_duracion_observada():
    samples = stopped(NOW - minutes(21), NOW)
    [alert] = alerts_for(samples)
    assert alert.vehicle_id == "V1" and alert.zone_id == "Z1"
    assert alert.started_at == NOW - minutes(21)
    assert alert.last_sample_at == NOW
    assert alert.observed_duration_s == 21 * 60
    assert alert.start_unknown is False
    assert alert.sample_count == 22


def test_exactamente_20_minutos_no_alerta_y_un_segundo_mas_si():
    exact = stopped(NOW - minutes(20), NOW)
    assert alerts_for(exact) == ()
    plus_one = [Sample("V1", eid(), NOW - minutes(20) - timedelta(seconds=1), *CENTER, 0.0)] + exact
    [alert] = alerts_for(plus_one)
    assert alert.observed_duration_s == 20 * 60 + 1


def test_el_paso_del_tiempo_sin_muestras_no_aumenta_la_duracion():
    samples = stopped(NOW - minutes(20), NOW - timedelta(seconds=60))
    # 19 min observados entre la primera y la última muestra.
    assert alerts_for(samples, now=NOW) == ()
    # Desde la primera muestra hasta `now` pasan casi 21 min, pero sin
    # muestras nuevas la duración demostrada sigue siendo 19 min.
    assert alerts_for(samples, now=NOW + timedelta(seconds=59)) == ()


# -- zona ---------------------------------------------------------------------

def test_frontera_del_radio():
    inside = north(*CENTER, ZONE.radius_m - 0.01)
    outside = north(*CENTER, ZONE.radius_m + 0.01)
    assert ZONE.contains(*inside) and not ZONE.contains(*outside)
    assert len(alerts_for(stopped(NOW - minutes(25), NOW, at=inside))) == 1
    assert alerts_for(stopped(NOW - minutes(25), NOW, at=outside)) == ()


def test_detenido_fuera_de_toda_zona_no_alerta():
    far = north(*CENTER, 5000)
    ev = evaluate_vehicle("V1", stopped(NOW - minutes(40), NOW, at=far), [ZONE], P, NOW)
    assert ev.alerts == () and ev.insufficient is None


def test_salir_de_la_zona_rompe_el_tramo():
    before = stopped(NOW - minutes(40), NOW - minutes(16))
    outside = [Sample("V1", eid(), NOW - minutes(15) - timedelta(seconds=30),
                      *north(*CENTER, 1000), 0.0)]
    after = stopped(NOW - minutes(15), NOW)
    assert alerts_for(before + outside + after) == ()


def test_zonas_superpuestas_generan_una_alerta_por_zona():
    other = Zone("Z2", "Zona 2", *north(*CENTER, 300), radius_m=400)  # se cruzan
    shared = north(*CENTER, 150)          # dentro de ambas
    only_z1 = north(*CENTER, -350)        # solo en Z1 (a 650 m de Z2)
    assert ZONE.contains(*shared) and other.contains(*shared)
    assert ZONE.contains(*only_z1) and not other.contains(*only_z1)

    both = alerts_for(stopped(NOW - minutes(30), NOW, at=shared), zones=(ZONE, other))
    assert sorted(a.zone_id for a in both) == ["Z1", "Z2"]
    assert len({a.alert_id for a in both}) == 2
    one = alerts_for(stopped(NOW - minutes(30), NOW, at=only_z1), zones=(ZONE, other))
    assert [a.zone_id for a in one] == ["Z1"]


# -- rupturas del tramo -------------------------------------------------------------

def test_muestra_de_movimiento_rompe_el_tramo():
    samples = stopped(NOW - minutes(40), NOW)
    moving = Sample("V1", eid(), NOW - minutes(10) - timedelta(seconds=30), *CENTER, 12.0)
    assert len(alerts_for(samples)) == 1
    # Tras el movimiento quedan 10:30 min observados: sin alerta.
    assert alerts_for(samples + [moving]) == ()


def test_velocidad_igual_al_umbral_cuenta_como_detencion():
    samples = stopped(NOW - minutes(25), NOW, speed=P.max_speed_kmh)
    assert len(alerts_for(samples)) == 1
    assert alerts_for(stopped(NOW - minutes(25), NOW, speed=P.max_speed_kmh + 0.1)) == ()


def test_ultima_muestra_en_movimiento_no_alerta():
    samples = stopped(NOW - minutes(40), NOW - minutes(1))
    samples.append(Sample("V1", eid(), NOW, *CENTER, 20.0))
    assert alerts_for(samples) == ()


def test_desplazamiento_respecto_a_la_primera_muestra():
    # Se arrastra 2 m por minuto: a los 25 min está a 50 m de la primera.
    def drifting(limit_m: float):
        out = []
        for i in range(31):
            meters = min(i * 2.0, limit_m)
            out.append(Sample("V1", eid(), NOW - minutes(30 - i), *north(*CENTER, meters), 0.0))
        return out

    # Hasta 49.99 m de la primera muestra: un solo tramo de 30 min.
    [alert] = alerts_for(drifting(49.99))
    assert alert.observed_duration_s == 30 * 60
    # Supera 50 m a los 26 min: el tramo se reinicia y quedan 4 min.
    assert alerts_for(drifting(60)) == ()


def test_desplazamiento_excesivo_inicia_un_tramo_nuevo():
    first = stopped(NOW - minutes(40), NOW - minutes(22))
    jumped = stopped(NOW - minutes(21), NOW, at=north(*CENTER, 80))
    [alert] = alerts_for(first + jumped)
    # El tramo nuevo empieza en la muestra que excedió el desplazamiento.
    assert alert.started_at == NOW - minutes(21)
    assert alert.observed_duration_s == 21 * 60


def test_interrupcion_de_muestras():
    before = stopped(NOW - minutes(30), NOW - minutes(15), step_s=60)
    # Separación de 121 s entre la última de `before` y la primera de `after`.
    after = stopped(NOW - minutes(15) + timedelta(seconds=121), NOW, step_s=60)
    assert alerts_for(before + after) == ()
    # Con 120 s exactos la continuidad se mantiene.
    after_ok = stopped(NOW - minutes(13), NOW, step_s=60)
    [alert] = alerts_for(before + after_ok)
    assert alert.started_at == NOW - minutes(30)


# -- antigüedad -------------------------------------------------------------

def test_ultima_muestra_desactualizada_da_evidencia_insuficiente():
    samples = stopped(NOW - minutes(40), NOW - minutes(3))
    samples.append(Sample("V1", eid(), NOW - timedelta(seconds=121), *CENTER, 0.0))
    ev = evaluate_vehicle("V1", samples, [ZONE], P, NOW)
    assert ev.alerts == ()
    assert ev.insufficient is not None
    assert ev.insufficient.reason == REASON_STALE
    assert ev.insufficient.last_sample_at == NOW - timedelta(seconds=121)
    # Con 120 s exactos todavía es vigente.
    fresh = evaluate_vehicle("V1", samples, [ZONE], P, NOW - timedelta(seconds=1))
    assert fresh.insufficient is None and len(fresh.alerts) == 1


def test_vehiculo_en_movimiento_desactualizado_tambien_es_insuficiente():
    samples = stopped(NOW - minutes(10), NOW - minutes(5), speed=40)
    ev = evaluate_vehicle("V1", samples, [ZONE], P, NOW)
    assert ev.insufficient is not None and ev.insufficient.reason == REASON_STALE


# -- duplicados y empates ------------------------------------------------------------

def test_duplicados_no_cambian_el_resultado():
    samples = stopped(NOW - minutes(25), NOW)
    [single] = alerts_for(samples)
    [doubled] = alerts_for(samples + list(reversed(samples)))
    assert doubled == single


def test_empate_de_timestamp_usa_el_mayor_event_id():
    base = stopped(NOW - minutes(30), NOW)
    tie_at = NOW - minutes(10)
    low_stop = Sample("V1", eid(1), tie_at, *CENTER, 0.0)
    high_moving = Sample("V1", "ffffffff-0000-0000-0000-000000000000", tie_at, *CENTER, 25.0)
    without_tie = [s for s in base if s.captured_at != tie_at]

    # Gana la muestra de mayor event_id (en movimiento): rompe el tramo.
    assert alerts_for(without_tie + [low_stop, high_moving]) == ()
    # Si la de mayor event_id es la detenida, el tramo sigue.
    low_moving = Sample("V1", eid(2), tie_at, *CENTER, 25.0)
    high_stop = Sample("V1", "ffffffff-0000-0000-0000-000000000001", tie_at, *CENTER, 0.0)
    assert len(alerts_for(without_tie + [low_moving, high_stop])) == 1


def test_el_orden_de_entrada_no_importa():
    samples = stopped(NOW - minutes(25), NOW)
    assert alerts_for(samples) == alerts_for(list(reversed(samples)))


# -- eventos atrasados -------------------------------------------------------------

def test_evento_atrasado_de_movimiento_retira_la_alerta():
    samples = stopped(NOW - minutes(30), NOW)
    [alert] = alerts_for(samples)
    late = Sample("V1", eid(), NOW - minutes(12) + timedelta(seconds=30), *CENTER, 30.0)
    # Siguiente evaluación con el historial que ya incluye el atrasado.
    assert alerts_for(samples + [late]) == ()


def test_evento_atrasado_que_cubre_una_interrupcion_crea_la_alerta():
    before = stopped(NOW - minutes(30), NOW - minutes(16))
    after = stopped(NOW - minutes(12), NOW)          # hueco de 4 min
    assert alerts_for(before + after) == ()
    late = stopped(NOW - minutes(15), NOW - minutes(13))
    [alert] = alerts_for(before + after + late)
    assert alert.started_at == NOW - minutes(30)


# -- ventana e identificadores -------------------------------------------------------

def test_inicio_truncado_por_la_ventana():
    window_start = NOW - timedelta(hours=2)
    samples = stopped(window_start + timedelta(seconds=30), NOW)
    [alert] = alerts_for(samples, window_start=window_start)
    assert alert.start_unknown is True
    assert alert.started_at == window_start + timedelta(seconds=30)  # mínimo observado

    # Si la primera muestra está a más de max_gap_s del límite, el inicio es exacto:
    # una muestra anterior a la ventana no podría continuar el tramo.
    late_start = stopped(window_start + timedelta(seconds=121), NOW)
    [exact] = alerts_for(late_start, window_start=window_start)
    assert exact.start_unknown is False


def test_identificador_estable_para_la_misma_detencion():
    samples = stopped(NOW - minutes(25), NOW)
    [a1] = alerts_for(samples)
    more = samples + stopped(NOW + minutes(1), NOW + minutes(3))
    [a2] = alerts_for(more, now=NOW + minutes(3))
    assert a1.alert_id == a2.alert_id
    assert a2.observed_duration_s == 28 * 60

    # Con inicio desconocido el id no depende de qué muestra quedó primera.
    w1 = NOW - timedelta(hours=2)
    long = stopped(w1 - minutes(10), NOW + minutes(5))
    [t1] = alerts_for([s for s in long if w1 <= s.captured_at <= NOW],
                      now=NOW, window_start=w1)
    w2 = w1 + minutes(5)
    [t2] = alerts_for([s for s in long if w2 <= s.captured_at <= NOW + minutes(5)],
                      now=NOW + minutes(5), window_start=w2)
    assert t1.start_unknown and t2.start_unknown
    assert t1.alert_id == t2.alert_id


def test_una_detencion_nueva_tiene_otro_identificador():
    first = stopped(NOW - minutes(60), NOW - minutes(31))
    moving = [Sample("V1", eid(), NOW - minutes(30) - timedelta(seconds=30), *CENTER, 20.0)]
    second = stopped(NOW - minutes(30), NOW)
    [old] = alerts_for(first, now=NOW - minutes(31))
    [new] = alerts_for(first + moving + second)
    assert old.alert_id != new.alert_id


def test_resultado_determinista():
    samples = stopped(NOW - minutes(25), NOW)
    assert evaluate_vehicle("V1", samples, [ZONE], P, NOW) == \
        evaluate_vehicle("V1", list(samples), [ZONE], P, NOW)


def test_haversine():
    # 1 grado de latitud ~ 111,195 km con el radio medio.
    assert abs(haversine_m(0, 0, 1, 0) - 111_195.08) < 1
    assert haversine_m(*CENTER, *CENTER) == 0


# -- flota y límites ----------------------------------------------------------------

def fleet_rows(vehicle_ids: list[str], minutes_stopped: int = 25) -> list[Sample]:
    rows = []
    for v in vehicle_ids:
        rows += stopped(NOW - minutes(minutes_stopped), NOW, vehicle=v)
    return rows


def test_evaluacion_completa_de_la_flota():
    rows = fleet_rows(["A", "B"])
    ev = evaluate_fleet(rows, [ZONE], P, NOW, NOW - timedelta(hours=2),
                        vehicles_in_window=2, max_vehicles=10, max_rows=1000)
    assert ev.complete is True and ev.incomplete_reasons == ()
    assert [a.vehicle_id for a in ev.alerts] == ["A", "B"]
    assert ev.vehicles_evaluated == 2 and ev.vehicles_not_evaluated == 0


def test_limite_de_vehiculos_marca_la_evaluacion_como_parcial():
    # La consulta solo trajo 2 de 3 vehículos (LIMIT max_vehicles).
    rows = fleet_rows(["A", "B"])
    ev = evaluate_fleet(rows, [ZONE], P, NOW, NOW - timedelta(hours=2),
                        vehicles_in_window=3, max_vehicles=2, max_rows=1000)
    assert ev.complete is False
    assert ev.incomplete_reasons == (REASON_MAX_VEHICLES,)
    assert ev.vehicles_evaluated == 2 and ev.vehicles_not_evaluated == 1


def test_limite_de_filas_descarta_el_vehiculo_cortado():
    rows = fleet_rows(["A", "B"])         # 26 filas por vehículo
    max_rows = 26 + 10                    # B queda cortado
    ev = evaluate_fleet(rows[:max_rows + 1], [ZONE], P, NOW, NOW - timedelta(hours=2),
                        vehicles_in_window=2, max_vehicles=10, max_rows=max_rows)
    assert ev.complete is False
    assert ev.incomplete_reasons == (REASON_MAX_ROWS,)
    # B no se evalúa con historial parcial (podría inventar una alerta corta).
    assert [a.vehicle_id for a in ev.alerts] == ["A"]
    assert ev.vehicles_evaluated == 1 and ev.vehicles_not_evaluated == 1


def test_limite_de_filas_justo_en_el_borde_de_un_vehiculo():
    rows = fleet_rows(["A", "B"])
    ev = evaluate_fleet(rows[:27], [ZONE], P, NOW, NOW - timedelta(hours=2),
                        vehicles_in_window=2, max_vehicles=10, max_rows=26)
    assert ev.complete is False
    assert [a.vehicle_id for a in ev.alerts] == ["A"]
    assert ev.vehicles_not_evaluated == 1
