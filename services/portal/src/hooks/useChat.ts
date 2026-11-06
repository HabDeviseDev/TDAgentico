import { useCallback, useEffect, useRef, useState } from "react";
import { isChatResponse, type ChatResponse, type ChatServiceStatus } from "../api/chat";

export interface ChatEntry {
  id: string;
  question: string;
  state: "pending" | "done" | "error";
  /** Respuesta estructurada (también en errores 429/502/503/504 del agente). */
  response?: ChatResponse;
  error?: string;
}

const STATUS_URL = "/v1/chat/status";
const CHAT_URL = "/v1/chat";
const STATUS_REFRESH_MS = 30_000;
const DEFAULT_MAX_CHARS = 500;

let counter = 0;
const newId = () => `q${Date.now()}-${++counter}`;

/**
 * Chat con el agente. Cada pregunta se envía sola (sin historial): el
 * historial es solo visual. Las solicitudes en curso se cancelan al
 * desmontar el componente.
 */
export function useChat() {
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [service, setService] = useState<ChatServiceStatus | null>(null);
  const [serviceError, setServiceError] = useState<string | null>(null);
  const controllers = useRef(new Map<string, AbortController>());

  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function load() {
      try {
        const resp = await fetch(STATUS_URL, { cache: "no-store" });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const body = (await resp.json()) as ChatServiceStatus;
        if (!disposed) {
          setService(body);
          setServiceError(null);
        }
      } catch (err) {
        if (!disposed) setServiceError(`El servicio del agente no responde (${String(err)})`);
      }
      if (!disposed) timer = setTimeout(load, STATUS_REFRESH_MS);
    }
    load();
    const active = controllers.current;
    return () => {
      disposed = true;
      clearTimeout(timer);
      active.forEach((c) => c.abort());
      active.clear();
    };
  }, []);

  const run = useCallback(async (id: string, question: string) => {
    const controller = new AbortController();
    controllers.current.set(id, controller);
    const update = (patch: Partial<ChatEntry>) =>
      setEntries((list) => list.map((e) => (e.id === id ? { ...e, ...patch } : e)));
    update({ state: "pending", error: undefined, response: undefined });
    try {
      const resp = await fetch(CHAT_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
        signal: controller.signal,
      });
      let body: unknown = null;
      try {
        body = await resp.json();
      } catch {
        body = null;
      }
      if (resp.ok && isChatResponse(body)) {
        update({ state: "done", response: body });
      } else if (isChatResponse(body)) {
        update({ state: "error", response: body, error: errorMessage(resp.status, body.status) });
      } else {
        const detail =
          body && typeof body === "object" && "detail" in body ? String((body as { detail: unknown }).detail) : "";
        update({ state: "error", error: errorMessage(resp.status, null, detail) });
      }
    } catch (err) {
      if (controller.signal.aborted) return;
      update({ state: "error", error: `No se pudo contactar el agente (${String(err)})` });
    } finally {
      controllers.current.delete(id);
    }
  }, []);

  const pending = entries.some((e) => e.state === "pending");

  const send = useCallback(
    (question: string) => {
      const q = question.trim();
      if (!q || pending) return false;
      const id = newId();
      setEntries((list) => [...list, { id, question: q, state: "pending" }]);
      void run(id, q);
      return true;
    },
    [pending, run],
  );

  const retry = useCallback(
    (id: string) => {
      if (pending) return;
      const entry = entries.find((e) => e.id === id);
      if (entry) void run(id, entry.question);
    },
    [entries, pending, run],
  );

  return {
    entries,
    pending,
    send,
    retry,
    service,
    serviceError,
    maxChars: service?.limits.max_question_chars ?? DEFAULT_MAX_CHARS,
  };
}

function errorMessage(httpStatus: number, status: string | null, detail = ""): string {
  switch (status) {
    case "not_configured":
      return "El agente requiere configuración en el servidor.";
    case "busy":
      return "Hay demasiadas consultas en curso. Reintenta en unos segundos.";
    case "timeout":
      return "La consulta superó el tiempo máximo.";
    case "model_error":
      return "El modelo de lenguaje no está disponible.";
    case "internal_error":
      return "Error interno del agente.";
  }
  if (httpStatus === 422) return detail || "La pregunta no es válida.";
  if (httpStatus === 502 || httpStatus === 503 || httpStatus === 504)
    return `El servicio del agente no está disponible (HTTP ${httpStatus}).`;
  return `Error inesperado (HTTP ${httpStatus}).`;
}
