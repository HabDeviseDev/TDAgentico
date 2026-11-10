import { DEFAULT_BACKOFF, type BackoffConfig } from "../core/backoff";
import { DEFAULT_SYNC, type SyncConfig } from "../core/sync";

/** Parámetros de la app (constantes del MVP; cambiar aquí y recompilar). */
export const APP_CONFIG: {
  captureIntervalMs: number;
  syncIntervalMs: number;
  requestTimeoutMs: number;
  sync: SyncConfig;
  backoff: BackoffConfig;
  listLimit: number;
} = {
  captureIntervalMs: 5_000,
  syncIntervalMs: 15_000,
  requestTimeoutMs: 10_000,
  sync: DEFAULT_SYNC,       // 50 eventos por lote (la API admite hasta BATCH_MAX_EVENTS)
  backoff: DEFAULT_BACKOFF, // 2 s, x2, tope 5 min, con jitter
  listLimit: 20,
};
