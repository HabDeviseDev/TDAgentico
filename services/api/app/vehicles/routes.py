"""Rutas HTTP de consulta de vehículos y stream SSE."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from ..settings import settings
from .repository import VehicleRepository
from .schemas import VehiclesResponse, utcnow
from .snapshots import SnapshotHub, TooManySubscribers
from .sse import client_stream

log = logging.getLogger("api.vehicles")
router = APIRouter(prefix="/v1/vehicles", tags=["vehicles"])


def get_repository(request: Request) -> VehicleRepository:
    return request.app.state.vehicle_repository


def get_hub(request: Request) -> SnapshotHub:
    return request.app.state.snapshot_hub


@router.get("", response_model=VehiclesResponse,
            responses={503: {"description": "La base de datos no respondió"}})
async def list_vehicles(repo: VehicleRepository = Depends(get_repository)) -> VehiclesResponse:
    try:
        vehicles = await repo.current_positions()
    except Exception as exc:  # noqa: BLE001
        log.error("GET /v1/vehicles falló: %r", exc)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "No se pudo leer la base de datos") from exc
    return VehiclesResponse(generated_at=utcnow(), count=len(vehicles), vehicles=vehicles)


@router.get("/stream", response_class=StreamingResponse,
            responses={200: {"content": {"text/event-stream": {}}},
                       503: {"description": "Demasiados clientes SSE"}})
async def stream_vehicles(hub: SnapshotHub = Depends(get_hub)) -> StreamingResponse:
    if hub.subscriber_count >= settings.sse_max_clients:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Demasiados clientes SSE")

    async def body():
        try:
            async for chunk in client_stream(hub, settings.sse_heartbeat_s):
                yield chunk
        except TooManySubscribers:
            return

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            # Indica a nginx que no acumule la respuesta.
            "X-Accel-Buffering": "no",
        },
    )
