"""Servicio de agente operativo: POST /v1/chat y estado.

Flujo: portal → (nginx) → este servicio → herramientas HTTP → API de flota.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Callable

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from langchain_core.language_models import BaseChatModel

from .agent import ChatAgent, RunState, collect
from .breaker import CircuitBreaker
from .fleet_client import FleetApiError, FleetClient
from .model import build_model, classify_model_error
from .schemas import ChatRequest, ChatResponse, ToolCallOut, Usage
from .settings import Settings, settings as default_settings
from .tools import FleetTools

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("agent")

DISCONNECT_POLL_S = 0.5


class ConcurrencyLimiter:
    """Cupos de chat simultáneos; rechaza en lugar de encolar."""

    def __init__(self, limit: int):
        self.limit = limit
        self.active = 0

    def try_acquire(self) -> bool:
        if self.active >= self.limit:
            return False
        self.active += 1
        return True

    def release(self) -> None:
        self.active = max(0, self.active - 1)


class AgentService:
    def __init__(self, cfg: Settings, model: BaseChatModel | None,
                 transport: httpx.AsyncBaseTransport | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.cfg = cfg
        self.breaker = CircuitBreaker(
            "fleet-api", failure_threshold=cfg.breaker_failure_threshold,
            recovery_timeout_s=cfg.breaker_recovery_s,
            half_open_max_calls=cfg.breaker_half_open_max_calls, clock=clock)
        self.client = FleetClient(
            cfg.fleet_api_url, self.breaker, connect_timeout_s=cfg.fleet_connect_timeout_s,
            read_timeout_s=cfg.fleet_read_timeout_s, retries=cfg.fleet_retries,
            retry_backoff_s=cfg.fleet_retry_backoff_s, max_response_bytes=cfg.fleet_max_response_bytes,
            transport=transport)
        self.tools = FleetTools(self.client, stale_after_s=cfg.stale_after_s,
                                max_items=cfg.max_tool_items)
        self.limiter = ConcurrencyLimiter(cfg.max_concurrent_chats)
        self.problem = None if model is not None else (cfg.configuration_problem()
                                                       or "Modelo no disponible")
        self.agent = (ChatAgent(model, self.tools, max_steps=cfg.max_steps,
                                max_tool_calls=cfg.max_tool_calls,
                                max_answer_chars=cfg.max_answer_chars)
                      if model is not None else None)

    async def aclose(self) -> None:
        await self.client.aclose()


def create_app(cfg: Settings | None = None, *, model: BaseChatModel | None = None,
               model_factory: Callable[[Settings], BaseChatModel | None] = build_model,
               transport: httpx.AsyncBaseTransport | None = None,
               clock: Callable[[], float] = time.monotonic) -> FastAPI:
    cfg = cfg or default_settings

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        chat_model = model if model is not None else model_factory(cfg)
        service = AgentService(cfg, chat_model, transport=transport, clock=clock)
        app.state.service = service
        if service.agent is None:
            log.warning("Agente sin configurar: %s. El resto del servicio funciona.", service.problem)
        else:
            log.info("Agente listo: proveedor=%s modelo=%s", cfg.provider, cfg.model)
        try:
            yield
        finally:
            await service.aclose()

    app = FastAPI(title="Fleet Agent", version="1.0.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/v1/chat/status")
    async def chat_status(request: Request) -> dict:
        svc: AgentService = request.app.state.service
        return {
            "configured": svc.agent is not None,
            "problem": svc.problem,
            "provider": cfg.provider,
            "model": cfg.model,
            "conversation": "independent",
            "limits": {
                "max_question_chars": cfg.max_question_chars,
                "max_concurrent_chats": cfg.max_concurrent_chats,
                "active_chats": svc.limiter.active,
                "request_timeout_s": cfg.request_timeout_s,
                "max_steps": cfg.max_steps,
                "max_tool_calls": cfg.max_tool_calls,
                "max_output_tokens": cfg.max_output_tokens,
                "max_tool_items": cfg.max_tool_items,
                "max_answer_chars": cfg.max_answer_chars,
            },
            "fleet_api": {"url": cfg.fleet_api_url, "circuit": svc.breaker.snapshot()},
        }

    @app.get("/v1/chat/fleet-check")
    async def fleet_check(request: Request) -> JSONResponse:
        """Consulta GET /v1/zones a través del cliente y el circuito (sin
        modelo). Sirve para ver el circuit breaker en acción."""
        svc: AgentService = request.app.state.service
        started = time.monotonic()
        try:
            body, meta = await svc.client.get_json("/v1/zones")
            payload = {"ok": True, "zones": body.get("count"), "attempts": meta["attempts"]}
            code = 200
        except FleetApiError as exc:
            payload = {"ok": False, "error": exc.public(),
                       "attempts": getattr(exc, "attempts", 0)}
            code = 503
        payload["duration_ms"] = round((time.monotonic() - started) * 1000)
        payload["circuit"] = svc.breaker.snapshot()
        return JSONResponse(payload, status_code=code)

    @app.post("/v1/chat", response_model=ChatResponse,
              responses={429: {"model": ChatResponse}, 502: {"model": ChatResponse},
                         503: {"model": ChatResponse}, 504: {"model": ChatResponse}})
    async def chat(body: ChatRequest, request: Request) -> JSONResponse:
        svc: AgentService = request.app.state.service
        request_id = uuid.uuid4().hex
        started = time.monotonic()
        state = RunState()
        question = body.question.strip()

        def respond(code: int) -> JSONResponse:
            return JSONResponse(_response(cfg, request_id, state, started).model_dump(mode="json"),
                                status_code=code)

        if not question or len(question) > cfg.max_question_chars:
            return JSONResponse({"detail": f"La pregunta debe tener entre 1 y {cfg.max_question_chars} caracteres"},
                                status_code=422)
        if svc.agent is None:
            state.status = "not_configured"
            state.warn("agent_not_configured", f"El agente requiere configuración: {svc.problem}")
            return respond(503)
        if not svc.limiter.try_acquire():
            state.status = "busy"
            state.warn("too_many_chats",
                       f"Hay {svc.limiter.limit} consultas en curso; reintenta en unos segundos")
            return respond(429)

        code = 200
        try:
            run_task = asyncio.create_task(_run_with_deadline(svc, question, state))
            watcher = asyncio.create_task(_cancel_on_disconnect(request, run_task))
            try:
                await run_task
            except asyncio.CancelledError:
                if watcher.done() and watcher.result():
                    log.info("chat %s: cliente desconectado, solicitud cancelada", request_id)
                    state.status = "internal_error"
                    return JSONResponse({"detail": "cliente desconectado"}, status_code=499)
                raise
            except TimeoutError:
                state.status = "timeout"
                state.warn("request_timeout",
                           f"La consulta superó el límite total de {cfg.request_timeout_s:g} s")
                state.answer = ("No pude completar la consulta a tiempo. Las consultas ya realizadas "
                                "aparecen abajo.")
                code = 504
            except Exception as exc:  # noqa: BLE001
                classified = classify_model_error(exc)
                if classified is not None:
                    state.status = "model_error"
                    state.warn(classified[0], classified[1])
                    state.answer = "El modelo de lenguaje no está disponible en este momento."
                    code = 502
                else:
                    log.exception("chat %s: error inesperado", request_id)
                    state.status = "internal_error"
                    state.warn("internal_error", "Error interno del agente")
                    code = 500
            finally:
                watcher.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watcher
        finally:
            svc.limiter.release()
        log.info("chat %s: status=%s steps=%d tools=%s duración=%dms", request_id, state.status,
                 state.steps, [(r.name, r.status) for r in state.records],
                 round((time.monotonic() - started) * 1000))
        return respond(code)

    return app


async def _run_with_deadline(svc: AgentService, question: str, state: RunState) -> None:
    async with asyncio.timeout(svc.cfg.request_timeout_s):
        await svc.agent.run(question, state)


async def _cancel_on_disconnect(request: Request, task: asyncio.Task) -> bool:
    """Cancela la consulta si el cliente cierra la conexión."""
    while not task.done():
        if await request.is_disconnected():
            task.cancel()
            return True
        await asyncio.sleep(DISCONNECT_POLL_S)
    return False


def _response(cfg: Settings, request_id: str, state: RunState, started: float) -> ChatResponse:
    gathered = collect(state, max_items=cfg.max_evidence_items)
    return ChatResponse(
        request_id=request_id,
        status=state.status if state.status != "running" else "internal_error",
        answer=state.answer,
        provider=cfg.provider,
        model=cfg.model,
        tools=[ToolCallOut(name=r.name, arguments=r.arguments, status=r.status, partial=r.partial,
                           truncated=r.truncated, duration_ms=r.duration_ms, attempts=r.attempts,
                           source=r.source, source_time_utc=r.source_time, counts=r.counts,
                           error=r.error) for r in state.records],
        sources=gathered["sources"],
        evidence=gathered["evidence"],
        vehicle_ids=gathered["vehicle_ids"],
        zone_ids=gathered["zone_ids"],
        warnings=gathered["warnings"],
        usage=Usage(steps=state.steps, max_steps=cfg.max_steps, tool_calls=state.tool_calls,
                    max_tool_calls=cfg.max_tool_calls,
                    duration_ms=round((time.monotonic() - started) * 1000)),
    )


app = create_app()
