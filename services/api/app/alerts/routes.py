"""GET /v1/zones y GET /v1/alerts."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..timed_read import ReadTimeout
from ..vehicles.schemas import utcnow
from .repository import AlertRepository
from .schemas import AlertsResponse, ZoneOut, ZonesResponse

log = logging.getLogger("api.alerts")
router = APIRouter(prefix="/v1", tags=["alerts"])

_503 = {503: {"description": "La base de datos no respondió"}}


def get_alert_repository(request: Request) -> AlertRepository:
    return request.app.state.alert_repository


def _unavailable(path: str, exc: Exception) -> HTTPException:
    if isinstance(exc, ReadTimeout):
        log.error("GET %s: %s", path, exc)
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                             "La base de datos no respondió a tiempo")
    log.error("GET %s falló: %r", path, exc)
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "No se pudo leer la base de datos")


@router.get("/zones", response_model=ZonesResponse, responses=_503)
async def list_zones(repo: AlertRepository = Depends(get_alert_repository)) -> ZonesResponse:
    """Zonas críticas activas."""
    try:
        zones = await repo.active_zones()
    except Exception as exc:  # noqa: BLE001
        raise _unavailable("/v1/zones", exc) from exc
    return ZonesResponse(generated_at=utcnow(), count=len(zones),
                         zones=[ZoneOut.of(z) for z in zones])


@router.get("/alerts", response_model=AlertsResponse, responses=_503)
async def list_alerts(repo: AlertRepository = Depends(get_alert_repository)) -> AlertsResponse:
    """Evaluación actual de alertas de detención, calculada en esta petición
    a partir del historial persistido."""
    try:
        evaluation = await repo.evaluate()
    except Exception as exc:  # noqa: BLE001
        raise _unavailable("/v1/alerts", exc) from exc
    return AlertsResponse.of(evaluation, repo.max_vehicles, repo.max_rows)
