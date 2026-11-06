"""Aplicación FastAPI: ingesta (Kafka) y consulta de vehículos (TimescaleDB + SSE)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import ingest
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
    repo = VehicleRepository(pool, settings.vehicles_max)
    hub = SnapshotHub(repo.current_positions, settings.vehicles_poll_interval_s,
                      max_subscribers=settings.sse_max_clients)
    app.state.vehicle_repository = repo
    app.state.snapshot_hub = hub
    hub_task = asyncio.create_task(hub.run(), name="snapshot-hub")
    try:
        yield
    finally:
        hub_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await hub_task
        await pool.close()
        await app.state.producer.stop()


app = FastAPI(title="Fleet API", version="1.1.0", lifespan=lifespan)
app.include_router(ingest.router)
app.include_router(vehicle_routes.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
