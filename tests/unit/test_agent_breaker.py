"""Circuit breaker y cliente HTTP del agente (dobles: reloj manual y API simulada)."""

from __future__ import annotations

import asyncio

import pytest

from agent_fakes import FLEET_URL, FakeFleetApi, ManualClock
from fleet_agent.breaker import BreakerState, CircuitBreaker, CircuitOpenError
from fleet_agent.fleet_client import FleetApiError, FleetClient


class Boom(Exception):
    pass


def breaker(clock, threshold=3, recovery=30.0, half_open=1) -> CircuitBreaker:
    return CircuitBreaker("dep", failure_threshold=threshold, recovery_timeout_s=recovery,
                          half_open_max_calls=half_open, clock=clock)


async def ok():
    return "ok"


async def fail():
    raise Boom()


def counts(exc: BaseException) -> bool:
    return isinstance(exc, Boom)


def run(coro):
    return asyncio.run(coro)


# -- estados ---------------------------------------------------------------------

def test_se_abre_tras_fallos_consecutivos_y_rechaza_sin_llamar():
    async def scenario():
        clock = ManualClock()
        b = breaker(clock, threshold=3)
        for _ in range(2):
            with pytest.raises(Boom):
                await b.run(fail, counts)
        assert b.state is BreakerState.CLOSED
        with pytest.raises(Boom):
            await b.run(fail, counts)
        assert b.state is BreakerState.OPEN

        called = []

        async def spy():
            called.append(1)
            return "x"
        with pytest.raises(CircuitOpenError) as exc:
            await b.run(spy, counts)
        assert called == []
        assert exc.value.retry_after_s == pytest.approx(30)
        clock.advance(10)
        with pytest.raises(CircuitOpenError) as exc:
            await b.run(spy, counts)
        assert exc.value.retry_after_s == pytest.approx(20)
        assert b.rejected_count == 2 and b.opened_count == 1

    run(scenario())


def test_un_exito_reinicia_los_fallos_consecutivos():
    async def scenario():
        b = breaker(ManualClock(), threshold=2)
        with pytest.raises(Boom):
            await b.run(fail, counts)
        await b.run(ok, counts)
        with pytest.raises(Boom):
            await b.run(fail, counts)
        assert b.state is BreakerState.CLOSED

    run(scenario())


def test_errores_que_no_cuentan_no_abren_el_circuito():
    async def scenario():
        b = breaker(ManualClock(), threshold=1)

        async def client_error():
            raise ValueError("4xx")
        for _ in range(3):
            with pytest.raises(ValueError):
                await b.run(client_error, counts)
        assert b.state is BreakerState.CLOSED

    run(scenario())


def test_semiabierto_prueba_limitada_y_recuperacion():
    async def scenario():
        clock = ManualClock()
        b = breaker(clock, threshold=1, recovery=30, half_open=1)
        with pytest.raises(Boom):
            await b.run(fail, counts)
        clock.advance(30)
        assert b.state is BreakerState.HALF_OPEN

        gate = asyncio.Event()

        async def slow_ok():
            await gate.wait()
            return "ok"
        trial = asyncio.create_task(b.run(slow_ok, counts))
        await asyncio.sleep(0)
        # Mientras la prueba está en curso, las demás se rechazan.
        with pytest.raises(CircuitOpenError) as exc:
            await b.run(ok, counts)
        assert exc.value.probing is True
        gate.set()
        assert await trial == "ok"
        assert b.state is BreakerState.CLOSED
        assert await b.run(ok, counts) == "ok"

    run(scenario())


def test_semiabierto_con_varias_pruebas_concurrentes():
    async def scenario():
        clock = ManualClock()
        b = breaker(clock, threshold=1, recovery=5, half_open=2)
        with pytest.raises(Boom):
            await b.run(fail, counts)
        clock.advance(5)
        gate = asyncio.Event()

        async def slow_ok():
            await gate.wait()
            return "ok"
        tasks = [asyncio.create_task(b.run(slow_ok, counts)) for _ in range(5)]
        await asyncio.sleep(0)
        gate.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        assert sum(r == "ok" for r in results) == 2
        assert sum(isinstance(r, CircuitOpenError) for r in results) == 3
        assert b.state is BreakerState.CLOSED

    run(scenario())


def test_prueba_fallida_vuelve_a_abrir_con_nuevo_plazo():
    async def scenario():
        clock = ManualClock()
        b = breaker(clock, threshold=1, recovery=30)
        with pytest.raises(Boom):
            await b.run(fail, counts)
        clock.advance(31)
        with pytest.raises(Boom):
            await b.run(fail, counts)            # la prueba falla
        assert b.state is BreakerState.OPEN
        assert b.opened_count == 2
        clock.advance(29)
        with pytest.raises(CircuitOpenError):
            await b.run(ok, counts)              # el plazo se reinició
        clock.advance(1)
        assert await b.run(ok, counts) == "ok"
        assert b.state is BreakerState.CLOSED

    run(scenario())


def test_cancelar_una_prueba_libera_el_cupo_sin_cambiar_el_estado():
    async def scenario():
        clock = ManualClock()
        b = breaker(clock, threshold=1, recovery=10)
        with pytest.raises(Boom):
            await b.run(fail, counts)
        clock.advance(10)

        async def hang():
            await asyncio.Event().wait()
        trial = asyncio.create_task(b.run(hang, counts))
        await asyncio.sleep(0)
        trial.cancel()
        with pytest.raises(asyncio.CancelledError):
            await trial
        assert b.state is BreakerState.HALF_OPEN
        assert await b.run(ok, counts) == "ok"   # el cupo quedó libre
        assert b.state is BreakerState.CLOSED

    run(scenario())


def test_resultado_tardio_de_otra_generacion_no_cambia_el_estado():
    async def scenario():
        clock = ManualClock()
        b = breaker(clock, threshold=1)
        gate = asyncio.Event()

        async def slow_ok():
            await gate.wait()
            return "ok"
        late = asyncio.create_task(b.run(slow_ok, counts))
        await asyncio.sleep(0)
        with pytest.raises(Boom):
            await b.run(fail, counts)            # abre el circuito
        gate.set()
        await late                               # éxito de una llamada anterior
        assert b.state is BreakerState.OPEN

    run(scenario())


# -- cliente HTTP -------------------------------------------------------------------

def client_for(api: FakeFleetApi, clock, retries=2, threshold=3) -> FleetClient:
    b = breaker(clock, threshold=threshold, recovery=30)
    return FleetClient(FLEET_URL, b, connect_timeout_s=0.5, read_timeout_s=0.5, retries=retries,
                       retry_backoff_s=0.0, max_response_bytes=1_000_000,
                       transport=api.transport())


def test_reintenta_errores_transitorios_y_cada_intento_pasa_por_el_circuito():
    async def scenario():
        api = FakeFleetApi()
        api.queue = ["error503", "timeout"]          # luego responde bien
        client = client_for(api, ManualClock(), retries=2, threshold=5)
        body, meta = await client.get("/v1/zones")
        return body, meta, client.breaker

    body, meta, b = run(scenario())
    assert body.count == 2
    assert meta["attempts"] == 3
    assert b.snapshot()["consecutive_failures"] == 0   # el éxito final reinicia


@pytest.mark.parametrize("mode,kind,failure,attempts", [
    ("timeout", "timeout", True, 3),
    ("connect", "connect", True, 3),
    ("error503", "http_5xx", True, 3),
    ("invalid_json", "invalid_response", True, 1),
    ("http404", "http_4xx", False, 1),
])
def test_clasificacion_de_errores(mode, kind, failure, attempts):
    async def scenario():
        api = FakeFleetApi()
        api.mode = mode
        client = client_for(api, ManualClock(), retries=2, threshold=10)
        with pytest.raises(FleetApiError) as exc:
            await client.get("/v1/alerts")
        return exc.value, api, client.breaker

    err, api, b = run(scenario())
    assert err.kind == kind
    assert err.counts_as_failure is failure
    assert len(api.requests) == attempts
    assert b.snapshot()["consecutive_failures"] == (attempts if failure else 0)


def test_rutas_fijas_y_url_de_configuracion():
    async def scenario():
        client = client_for(FakeFleetApi(), ManualClock())
        with pytest.raises(ValueError):
            await client.get("http://otro-host/v1/alerts")
        with pytest.raises(ValueError):
            await client.get("/v1/positions")

    run(scenario())


def test_circuito_abierto_no_llama_a_la_api_y_no_reintenta():
    async def scenario():
        clock = ManualClock()
        api = FakeFleetApi()
        api.mode = "error503"
        client = client_for(api, clock, retries=0, threshold=2)
        for _ in range(2):
            with pytest.raises(FleetApiError):
                await client.get("/v1/zones")
        before = len(api.requests)
        with pytest.raises(FleetApiError) as exc:
            await client.get("/v1/zones")
        assert exc.value.kind == "circuit_open"
        assert len(api.requests) == before
        # Recuperación: pasa el plazo, la API responde y el circuito se cierra.
        api.mode = "ok"
        clock.advance(30)
        body, _ = await client.get("/v1/zones")
        assert body.count == 2
        assert client.breaker.state is BreakerState.CLOSED

    run(scenario())
