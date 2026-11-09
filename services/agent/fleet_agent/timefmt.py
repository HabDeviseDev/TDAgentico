"""Presentación de fechas en la zona horaria del usuario.

Los datos se guardan, se transportan (contratos de la API) y se comparan en
UTC; las antigüedades se calculan entre fechas UTC. Solo el texto que ve el
usuario se convierte, una vez y aquí, con `zoneinfo` de la biblioteca
estándar: sin desfases fijos, así se respetan el cambio de día y cualquier
regla de la zona.

Formato: "09/11/2026 10:26:28 America/Bogota" (la zona va siempre explícita).
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_DISPLAY_TIMEZONE = "America/Bogota"
DISPLAY_FORMAT = "%d/%m/%Y %H:%M:%S"


class LocalClock:
    """Convierte fechas con zona horaria a texto en la zona de presentación."""

    def __init__(self, tz_name: str = DEFAULT_DISPLAY_TIMEZONE):
        try:
            self.zone = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Zona horaria de presentación desconocida: {tz_name!r}") from exc
        self.name = tz_name

    def format(self, value: datetime) -> str:
        if value.tzinfo is None or value.utcoffset() is None:
            # Nunca se supone una zona: los contratos exigen fechas con zona.
            raise ValueError("La fecha no tiene zona horaria")
        return f"{value.astimezone(self.zone).strftime(DISPLAY_FORMAT)} {self.name}"

    def format_optional(self, value: datetime | None) -> str | None:
        return None if value is None else self.format(value)
