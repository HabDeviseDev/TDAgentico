"""Configuración del servicio de agente (solo variables de entorno).

Las credenciales del proveedor se leen aquí y nunca salen del backend.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _f(name: str, default: str) -> float:
    return float(os.getenv(name, default))


def _i(name: str, default: str) -> int:
    return int(os.getenv(name, default))


def _b(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "si", "sí"}


@dataclass(frozen=True)
class Settings:
    # Proveedor del modelo.
    provider: str = os.getenv("AGENT_PROVIDER", "anthropic").strip().lower()
    model: str = os.getenv("AGENT_MODEL", "claude-opus-5-5").strip()
    anthropic_api_key: str = field(default=os.getenv("ANTHROPIC_API_KEY", "").strip(), repr=False)
    # Esfuerzo de razonamiento (low | medium | high | xhigh | max).
    effort: str = os.getenv("AGENT_EFFORT", "low").strip()
    # Fallback del servidor ante un rechazo (refusal) del modelo principal.
    refusal_fallback: bool = _b("AGENT_REFUSAL_FALLBACK", "true")
    model_timeout_s: float = _f("AGENT_MODEL_TIMEOUT_S", "30")
    model_max_retries: int = _i("AGENT_MODEL_MAX_RETRIES", "1")

    # Destino de las herramientas: SOLO desde configuración del servidor.
    fleet_api_url: str = os.getenv("FLEET_API_URL", "http://localhost:8000").rstrip("/")
    fleet_connect_timeout_s: float = _f("FLEET_CONNECT_TIMEOUT_S", "2")
    fleet_read_timeout_s: float = _f("FLEET_READ_TIMEOUT_S", "6")
    # Reintentos adicionales para lecturas (GET) ante errores transitorios.
    fleet_retries: int = _i("FLEET_RETRIES", "2")
    fleet_retry_backoff_s: float = _f("FLEET_RETRY_BACKOFF_S", "0.3")
    fleet_max_response_bytes: int = _i("FLEET_MAX_RESPONSE_BYTES", str(5 * 1024 * 1024))

    # Circuit breaker hacia la API de flota (uno por instancia y dependencia).
    breaker_failure_threshold: int = _i("CB_FAILURE_THRESHOLD", "3")
    breaker_recovery_s: float = _f("CB_RECOVERY_S", "30")
    breaker_half_open_max_calls: int = _i("CB_HALF_OPEN_MAX_CALLS", "1")

    # Límites del chat.
    max_question_chars: int = _i("AGENT_MAX_QUESTION_CHARS", "500")
    max_concurrent_chats: int = _i("AGENT_MAX_CONCURRENT_CHATS", "4")
    request_timeout_s: float = _f("AGENT_REQUEST_TIMEOUT_S", "60")
    max_steps: int = _i("AGENT_MAX_STEPS", "5")
    max_tool_calls: int = _i("AGENT_MAX_TOOL_CALLS", "6")
    max_output_tokens: int = _i("AGENT_MAX_OUTPUT_TOKENS", "4096")
    max_tool_items: int = _i("AGENT_MAX_TOOL_ITEMS", "25")
    max_answer_chars: int = _i("AGENT_MAX_ANSWER_CHARS", "4000")
    max_evidence_items: int = _i("AGENT_MAX_EVIDENCE_ITEMS", "50")

    # Igual que el umbral del portal (PORTAL_STALE_AFTER_SECONDS).
    stale_after_s: float = _f("STALE_AFTER_SECONDS", "120")

    # Zona horaria IANA en la que se presentan las fechas al usuario (texto de
    # la respuesta, fuentes y evidencias). Los datos siguen en UTC.
    display_timezone: str = os.getenv("AGENT_DISPLAY_TIMEZONE", "America/Bogota").strip()

    def configuration_problem(self) -> str | None:
        """Motivo por el que el agente no puede usar el modelo, o None."""
        if self.provider != "anthropic":
            return f"Proveedor no soportado: {self.provider!r} (soportado: anthropic)"
        if not self.anthropic_api_key:
            return "Falta ANTHROPIC_API_KEY en el servicio agent"
        if not self.model:
            return "Falta AGENT_MODEL"
        return None


settings = Settings()
