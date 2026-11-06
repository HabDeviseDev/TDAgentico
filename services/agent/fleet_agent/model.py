"""Proveedor del modelo de lenguaje (LangChain).

Proveedor implementado: Anthropic (`langchain-anthropic`), modelo por defecto
`claude-opus-5-5`. Notas de configuración para ese modelo:
- No admite desactivar el razonamiento: el gasto se controla con `effort`
  (`AGENT_EFFORT`, por defecto `low`, suficiente para consultas de lectura).
- No se envía `temperature` ni `tool_choice` forzado (el modelo los rechaza).
- `max_tokens` (AGENT_MAX_OUTPUT_TOKENS) incluye el razonamiento.
- Con `AGENT_REFUSAL_FALLBACK=true` se pide al servidor el fallback por
  defecto ante un rechazo (`fallbacks: "default"` + beta).
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel

from .settings import Settings

FALLBACK_BETA = "server-side-fallback-2026-07-01"


def build_model(cfg: Settings) -> BaseChatModel | None:
    """Modelo configurado o None si falta configuración (no lanza)."""
    if cfg.configuration_problem():
        return None
    from langchain_anthropic import ChatAnthropic

    kwargs: dict[str, Any] = {}
    betas: list[str] = []
    if cfg.refusal_fallback:
        betas.append(FALLBACK_BETA)
        kwargs["model_kwargs"] = {"fallbacks": "default"}
    return ChatAnthropic(
        model=cfg.model,
        api_key=cfg.anthropic_api_key,
        max_tokens=cfg.max_output_tokens,
        timeout=cfg.model_timeout_s,
        max_retries=cfg.model_max_retries,
        output_config={"effort": cfg.effort},
        betas=betas or None,
        **kwargs,
    )


def classify_model_error(exc: BaseException) -> tuple[str, str] | None:
    """(código, mensaje público) para errores del proveedor; None si no lo es.
    El mensaje nunca incluye la credencial ni el cuerpo de la respuesta."""
    try:
        import anthropic
    except ImportError:  # pragma: no cover
        return None
    if isinstance(exc, anthropic.AuthenticationError):
        return "model_auth_failed", "El proveedor rechazó la credencial configurada (ANTHROPIC_API_KEY)"
    if isinstance(exc, anthropic.PermissionDeniedError):
        return "model_forbidden", "La credencial no tiene acceso al modelo configurado"
    if isinstance(exc, anthropic.NotFoundError):
        return "model_not_found", "El modelo configurado (AGENT_MODEL) no existe o no está disponible"
    if isinstance(exc, anthropic.RateLimitError):
        return "model_rate_limited", "El proveedor del modelo limitó la tasa de solicitudes; reintenta más tarde"
    if isinstance(exc, anthropic.BadRequestError):
        return "model_bad_request", "El proveedor rechazó la solicitud (revisa modelo y parámetros)"
    if isinstance(exc, anthropic.APITimeoutError):
        return "model_timeout", "El proveedor del modelo no respondió a tiempo"
    if isinstance(exc, anthropic.APIConnectionError):
        return "model_unavailable", "No se pudo conectar con el proveedor del modelo"
    if isinstance(exc, anthropic.APIStatusError):
        return "model_unavailable", f"El proveedor del modelo respondió {exc.status_code}"
    return None
