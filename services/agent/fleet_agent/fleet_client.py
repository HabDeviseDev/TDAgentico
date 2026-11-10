"""Cliente HTTP de solo lectura hacia la API de flota.

- La URL base sale de la configuración del servidor; las rutas son fijas
  (`ALLOWED_PATHS`). Nada que venga del modelo o del usuario forma una URL.
- Timeouts separados de conexión y lectura.
- Reintentos acotados (solo GET, idempotentes) ante errores transitorios.
- Cada intento pasa por el circuit breaker.

Clasificación de errores (`FleetApiError.kind`):

| kind              | Cuenta como fallo del circuito | Se reintenta |
|-------------------|--------------------------------|--------------|
| timeout           | sí                             | sí           |
| connect           | sí (conexión rechazada, DNS…)  | sí           |
| http_5xx          | sí                             | solo 502/503/504 |
| invalid_response  | sí (JSON inválido, demasiado grande o que no cumple el contrato) | no |
| http_4xx          | no (la API respondió; error de la petición) | no |
| circuit_open      | — (rechazada sin llamar)       | no           |
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

import httpx
from pydantic import BaseModel, ValidationError

from .api_contracts import RESPONSE_CONTRACTS
from .breaker import CircuitBreaker, CircuitOpenError

log = logging.getLogger("agent.fleet")

ALLOWED_PATHS = frozenset(RESPONSE_CONTRACTS)
RETRYABLE_STATUS = frozenset({502, 503, 504})


class FleetApiError(Exception):
    def __init__(self, kind: str, message: str, *, status_code: int | None = None,
                 counts_as_failure: bool, retryable: bool, retry_after_s: float | None = None):
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code
        self.counts_as_failure = counts_as_failure
        self.retryable = retryable
        self.retry_after_s = retry_after_s

    def public(self) -> dict:
        return {"kind": self.kind, "message": str(self), "status_code": self.status_code,
                "retry_after_s": self.retry_after_s}


def _is_failure(exc: BaseException) -> bool:
    return isinstance(exc, FleetApiError) and exc.counts_as_failure


class FleetClient:
    def __init__(self, base_url: str, breaker: CircuitBreaker, *, connect_timeout_s: float,
                 read_timeout_s: float, retries: int, retry_backoff_s: float,
                 max_response_bytes: int, transport: httpx.AsyncBaseTransport | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.base_url = base_url
        self.breaker = breaker
        self._retries = max(0, retries)
        self._backoff_s = retry_backoff_s
        self._max_bytes = max_response_bytes
        self._sleep = sleep
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(connect=connect_timeout_s, read=read_timeout_s,
                                  write=connect_timeout_s, pool=connect_timeout_s),
            transport=transport,
            follow_redirects=False,
            headers={"Accept": "application/json", "User-Agent": "fleet-agent"},
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get(self, path: str) -> tuple[BaseModel, dict]:
        """GET a una ruta permitida. Devuelve (respuesta validada con su
        contrato, meta con intentos y duración)."""
        if path not in ALLOWED_PATHS:
            raise ValueError(f"ruta no permitida: {path}")
        started = time.monotonic()
        attempt = 0
        while True:
            attempt += 1
            try:
                body = await self.breaker.run(lambda: self._once(path), _is_failure)
                return body, {"attempts": attempt,
                              "duration_ms": round((time.monotonic() - started) * 1000)}
            except CircuitOpenError as exc:
                raise FleetApiError(
                    "circuit_open",
                    "La API de flota no está disponible (circuito abierto); no se llamó",
                    counts_as_failure=False, retryable=False,
                    retry_after_s=round(exc.retry_after_s, 1)) from exc
            except FleetApiError as exc:
                if not exc.retryable or attempt > self._retries:
                    exc.attempts = attempt  # type: ignore[attr-defined]
                    raise
                log.info("GET %s falló (%s), reintento %d/%d", path, exc.kind, attempt, self._retries)
                await self._sleep(self._backoff_s * attempt)

    async def _once(self, path: str) -> BaseModel:
        try:
            resp = await self._http.get(path)
        except httpx.TimeoutException as exc:
            raise FleetApiError("timeout", f"La API de flota no respondió a tiempo ({type(exc).__name__})",
                                counts_as_failure=True, retryable=True) from exc
        except httpx.TransportError as exc:
            raise FleetApiError("connect", f"No se pudo conectar con la API de flota ({type(exc).__name__})",
                                counts_as_failure=True, retryable=True) from exc
        status = resp.status_code
        if status >= 500:
            raise FleetApiError("http_5xx", f"La API de flota respondió {status}: {_detail(resp)}",
                                status_code=status, counts_as_failure=True,
                                retryable=status in RETRYABLE_STATUS)
        if status >= 400:
            raise FleetApiError("http_4xx", f"La API de flota rechazó la consulta ({status})",
                                status_code=status, counts_as_failure=False, retryable=False)
        if len(resp.content) > self._max_bytes:
            raise FleetApiError("invalid_response", "Respuesta de la API de flota demasiado grande",
                                status_code=status, counts_as_failure=True, retryable=False)
        # El contrato se valida aquí, dentro del intento: una respuesta que no lo
        # cumple cuenta como fallo de la dependencia antes de registrar un éxito.
        try:
            return RESPONSE_CONTRACTS[path].model_validate_json(resp.content)
        except ValidationError as exc:
            # Al log interno solo van ubicación y tipo de cada error, sin valores.
            log.warning("GET %s: respuesta fuera de contrato (%d errores): %s", path,
                        exc.error_count(),
                        [(".".join(str(p) for p in e["loc"]), e["type"]) for e in exc.errors()[:10]])
            raise FleetApiError(
                "invalid_response",
                "La API de flota devolvió una respuesta que no cumple el contrato esperado",
                status_code=status, counts_as_failure=True, retryable=False) from None


def _detail(resp: httpx.Response) -> str:
    try:
        detail = resp.json().get("detail")
        if isinstance(detail, str):
            return detail[:200]
    except Exception:  # noqa: BLE001
        pass
    return "sin detalle"
