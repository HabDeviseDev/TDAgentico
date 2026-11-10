"""Identificadores compartidos por la ingesta y los consumidores de la API.

`VehicleId` es la regla de `vehicle_id` del evento de posición: texto de 1 a
64 caracteres que, sin espacios en los extremos, no queda vacío. Admite
cualquier carácter (mayúsculas, minúsculas, espacios internos, acentos,
barras…) y distingue mayúsculas de minúsculas: `BOG-001` y `bog-001` son
vehículos distintos.

El orden de validación es el original del evento: primero la longitud del
texto recibido y luego se quitan los espacios de los extremos.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator, Field

VEHICLE_ID_MAX_LENGTH = 64


def _strip_non_empty(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("vehicle_id no puede estar vacío")
    return value


VehicleId = Annotated[
    str,
    Field(min_length=1, max_length=VEHICLE_ID_MAX_LENGTH),
    AfterValidator(_strip_non_empty),
]
