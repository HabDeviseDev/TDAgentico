export interface BackoffConfig {
  /** Espera del primer reintento (ms). */
  baseMs: number;
  /** Tope de la espera (ms). */
  maxMs: number;
  factor: number;
}

export const DEFAULT_BACKOFF: BackoffConfig = { baseMs: 2_000, maxMs: 5 * 60_000, factor: 2 };

/**
 * Espera creciente con jitter ("equal jitter"): la mitad fija y la otra mitad
 * aleatoria. `attempts` es la cantidad de intentos ya hechos (>= 1).
 * Resultado en [techo/2, techo], con techo = min(max, base * factor^(attempts-1)).
 */
export function backoffDelayMs(attempts: number, cfg: BackoffConfig, random: () => number): number {
  const exp = Math.min(cfg.maxMs, cfg.baseMs * cfg.factor ** Math.max(0, attempts - 1));
  return Math.round(exp / 2 + random() * (exp / 2));
}
