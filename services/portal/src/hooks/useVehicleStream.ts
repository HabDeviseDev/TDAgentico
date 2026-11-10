import { useEffect, useReducer } from "react";
import type {
  AlertsEvent, AlertsStatusEvent, HeartbeatEvent, SnapshotEvent, StatusEvent,
} from "../api/types";
import { fleetReducer, initialState } from "../state/fleetState";

const MAX_BACKOFF_MS = 30_000;

/**
 * Conecta a /v1/vehicles/stream. El SSE es la única fuente de posiciones y
 * alertas del portal: al conectar (y en cada reconexión) llegan el snapshot y
 * el resultado de alertas completos, así no hay carrera entre una carga REST
 * y el tiempo real.
 */
export function useVehicleStream(url: string, connectionTimeoutSeconds: number) {
  const [state, dispatch] = useReducer(fleetReducer, initialState);

  useEffect(() => {
    let source: EventSource | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let disposed = false;
    let backoffMs = 1_000;
    let lastMessageAt = Date.now();

    const parse = <T,>(handler: (data: T, at: number) => void) => (e: MessageEvent<string>) => {
      lastMessageAt = Date.now();
      try {
        handler(JSON.parse(e.data) as T, lastMessageAt);
      } catch (err) {
        console.error("Evento SSE inválido", err);
      }
    };

    const reconnectLater = () => {
      source?.close();
      source = null;
      if (disposed) return;
      clearTimeout(retryTimer);
      retryTimer = setTimeout(connect, backoffMs);
      backoffMs = Math.min(backoffMs * 2, MAX_BACKOFF_MS);
    };

    function connect() {
      dispatch({ type: "connecting" });
      lastMessageAt = Date.now();
      const es = new EventSource(url);
      source = es;
      es.onopen = () => {
        backoffMs = 1_000;
        lastMessageAt = Date.now();
        dispatch({ type: "open", at: lastMessageAt });
      };
      es.addEventListener(
        "snapshot",
        parse<SnapshotEvent>((event, at) => dispatch({ type: "snapshot", event, at })),
      );
      es.addEventListener(
        "status",
        parse<StatusEvent>((event, at) => dispatch({ type: "status", event, at })),
      );
      es.addEventListener(
        "alerts",
        parse<AlertsEvent>((event, at) => dispatch({ type: "alerts", event, at })),
      );
      es.addEventListener(
        "alerts_status",
        parse<AlertsStatusEvent>((event, at) => dispatch({ type: "alerts_status", event, at })),
      );
      es.addEventListener(
        "heartbeat",
        parse<HeartbeatEvent>((_event, at) => dispatch({ type: "heartbeat", at })),
      );
      es.onerror = () => {
        dispatch({ type: "lost" });
        // CONNECTING: el navegador reintenta solo. CLOSED (p. ej. respuesta
        // 503 o no-SSE): hay que reconectar manualmente.
        if (es.readyState === EventSource.CLOSED) reconnectLater();
      };
    }

    // Sin mensajes ni heartbeats durante demasiado tiempo: la conexión está
    // colgada aunque el navegador no lo haya detectado.
    const watchdog = setInterval(() => {
      if (source && Date.now() - lastMessageAt > connectionTimeoutSeconds * 1000) {
        dispatch({ type: "lost" });
        reconnectLater();
      }
    }, 2_000);

    connect();
    return () => {
      disposed = true;
      clearInterval(watchdog);
      clearTimeout(retryTimer);
      source?.close();
    };
  }, [url, connectionTimeoutSeconds]);

  return [state, dispatch] as const;
}
