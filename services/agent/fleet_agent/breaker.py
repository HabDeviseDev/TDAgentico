"""Circuit breaker para las llamadas del agente a la API de flota.

Estados:
- cerrado: las llamadas pasan; `failure_threshold` fallos CONSECUTIVOS lo abren.
- abierto: se rechaza sin llamar (CircuitOpenError) hasta que pasan
  `recovery_timeout_s` segundos desde la apertura.
- semiabierto: se permiten como máximo `half_open_max_calls` pruebas a la vez;
  el resto se rechaza. Una prueba correcta lo cierra; una fallida lo vuelve a
  abrir con un nuevo plazo.

Concurrencia: el servicio corre en un solo event loop por proceso. Todas las
transiciones son métodos síncronos (sin `await` en medio), así que cada una
es atómica respecto de las demás corrutinas. Cada permiso guarda la
"generación" del estado en que se emitió: el resultado de una llamada que
empezó antes de un cambio de estado no lo altera (p. ej. un éxito tardío no
cierra un circuito que se abrió mientras tanto).

Cancelación: una llamada cancelada libera su permiso sin contar como éxito ni
como fallo (libera el cupo de prueba en semiabierto).

Alcance: una instancia por proceso y por dependencia (la API de flota). Todas
las solicitudes de chat de esa instancia comparten el mismo circuito; otras
instancias del servicio tienen el suyo.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, TypeVar

log = logging.getLogger("agent.breaker")
T = TypeVar("T")


class BreakerState(str, enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    def __init__(self, name: str, retry_after_s: float, probing: bool = False):
        self.name = name
        self.retry_after_s = max(0.0, retry_after_s)
        self.probing = probing
        reason = ("hay una prueba de recuperación en curso" if probing
                  else f"reintento en {self.retry_after_s:.0f} s")
        super().__init__(f"Circuito '{name}' abierto: {reason}")


@dataclass
class Permit:
    generation: int
    trial: bool
    done: bool = False


class CircuitBreaker:
    def __init__(self, name: str, *, failure_threshold: int, recovery_timeout_s: float,
                 half_open_max_calls: int = 1, clock: Callable[[], float] = time.monotonic):
        if failure_threshold < 1 or half_open_max_calls < 1 or recovery_timeout_s <= 0:
            raise ValueError("configuración de circuit breaker inválida")
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout_s = recovery_timeout_s
        self.half_open_max_calls = half_open_max_calls
        self._clock = clock
        self._state = BreakerState.CLOSED
        self._generation = 0
        self._consecutive_failures = 0
        self._opened_at = 0.0
        self._trials_in_flight = 0
        self.opened_count = 0
        self.rejected_count = 0

    # -- estado ------------------------------------------------------------------
    @property
    def state(self) -> BreakerState:
        """Estado efectivo: un circuito abierto cuyo plazo venció se informa
        como semiabierto (la transición se aplica en la siguiente llamada)."""
        if self._state is BreakerState.OPEN and self._recovery_elapsed():
            return BreakerState.HALF_OPEN
        return self._state

    def snapshot(self) -> dict:
        retry_after = 0.0
        if self._state is BreakerState.OPEN:
            retry_after = max(0.0, self.recovery_timeout_s - (self._clock() - self._opened_at))
        return {
            "name": self.name,
            "state": self.state.value,
            "consecutive_failures": self._consecutive_failures,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout_s": self.recovery_timeout_s,
            "half_open_max_calls": self.half_open_max_calls,
            "retry_after_s": round(retry_after, 1),
            "opened_count": self.opened_count,
            "rejected_count": self.rejected_count,
        }

    def _recovery_elapsed(self) -> bool:
        return self._clock() - self._opened_at >= self.recovery_timeout_s

    def _transition(self, state: BreakerState) -> None:
        if state is not self._state:
            log.warning("Circuito '%s': %s -> %s", self.name, self._state.value, state.value)
        self._state = state
        self._generation += 1
        self._trials_in_flight = 0
        if state is BreakerState.OPEN:
            self._opened_at = self._clock()
            self.opened_count += 1
        if state is BreakerState.CLOSED:
            self._consecutive_failures = 0

    # -- permisos ------------------------------------------------------------------
    def acquire(self) -> Permit:
        if self._state is BreakerState.OPEN and self._recovery_elapsed():
            self._transition(BreakerState.HALF_OPEN)
        if self._state is BreakerState.OPEN:
            self.rejected_count += 1
            raise CircuitOpenError(self.name, self.recovery_timeout_s - (self._clock() - self._opened_at))
        if self._state is BreakerState.HALF_OPEN:
            if self._trials_in_flight >= self.half_open_max_calls:
                self.rejected_count += 1
                raise CircuitOpenError(self.name, 0.0, probing=True)
            self._trials_in_flight += 1
            return Permit(self._generation, trial=True)
        return Permit(self._generation, trial=False)

    def _finish(self, permit: Permit) -> bool:
        """Marca el permiso como usado; True si sigue vigente."""
        if permit.done:
            return False
        permit.done = True
        current = permit.generation == self._generation
        if current and permit.trial:
            self._trials_in_flight = max(0, self._trials_in_flight - 1)
        return current

    def record_success(self, permit: Permit) -> None:
        if not self._finish(permit):
            return
        if self._state is BreakerState.HALF_OPEN:
            self._transition(BreakerState.CLOSED)
        else:
            self._consecutive_failures = 0

    def record_failure(self, permit: Permit) -> None:
        if not self._finish(permit):
            return
        if self._state is BreakerState.HALF_OPEN:
            self._transition(BreakerState.OPEN)
            return
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.failure_threshold:
            self._transition(BreakerState.OPEN)

    def release(self, permit: Permit) -> None:
        """Libera sin resultado (cancelación)."""
        self._finish(permit)

    # -- envoltorio --------------------------------------------------------------
    async def run(self, op: Callable[[], Awaitable[T]],
                  is_failure: Callable[[BaseException], bool]) -> T:
        """Ejecuta `op` bajo el circuito. `is_failure` decide qué excepciones
        cuentan como fallo de la dependencia; las demás (p. ej. un 404) prueban
        que la dependencia responde y cuentan como éxito."""
        permit = self.acquire()
        try:
            result = await op()
        except asyncio.CancelledError:
            self.release(permit)
            raise
        except BaseException as exc:
            if is_failure(exc):
                self.record_failure(permit)
            else:
                self.record_success(permit)
            raise
        self.record_success(permit)
        return result
