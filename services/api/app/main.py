"""Aplicación FastAPI: ingesta (Kafka) y consulta de vehículos (TimescaleDB + SSE)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import ingest
from .alerts import routes as alert_routes
from .alerts.hub import AlertHub
from .alerts.repository import AlertRepository
from .db import create_pool
from .settings import settings
from .vehicles import routes as vehicle_routes
from .vehicles.repository import VehicleRepository
from .vehicles.snapshots import SnapshotHub

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("api")

# Reexportado para las pruebas existentes.
get_producer = ingest.get_producer


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.producer = await ingest.start_producer()

    pool = create_pool(settings)
    # wait=False: la API arranca aunque la BD aún no responda; el hub
    # informará `degraded` hasta que la conexión esté disponible.
    await pool.open(wait=False)
    repo = VehicleRepository(pool, settings.vehicles_max, settings.db_read_timeout_s)
    hub = SnapshotHub(repo.current_positions, settings.vehicles_poll_interval_s,
                      max_subscribers=settings.sse_max_clients)
    app.state.vehicle_repository = repo
    app.state.snapshot_hub = hub

    alert_repo = AlertRepository(pool, settings.stop_rule_params(),
                                 max_vehicles=settings.alerts_max_vehicles,
                                 max_rows=settings.alerts_max_rows,
                                 read_timeout_s=settings.db_read_timeout_s)
    alert_hub = AlertHub(alert_repo.evaluate, settings.alerts_eval_interval_s,
                         max_vehicles=settings.alerts_max_vehicles,
                         max_rows=settings.alerts_max_rows,
                         max_subscribers=settings.sse_max_clients)
    app.state.alert_repository = alert_repo
    app.state.alert_hub = alert_hub
    # El stream SSE de vehículos también transmite las alertas.
    app.state.stream_extra_hubs = (alert_hub,)

    tasks = [asyncio.create_task(hub.run(), name="snapshot-hub"),
             asyncio.create_task(alert_hub.run(), name="alert-hub")]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await pool.close()
        await app.state.producer.stop()


app = FastAPI(title="Fleet API", version="1.2.0", lifespan=lifespan)
app.include_router(ingest.router)
app.include_router(vehicle_routes.router)
app.include_router(alert_routes.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
