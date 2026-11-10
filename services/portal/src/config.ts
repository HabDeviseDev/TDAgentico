export interface PortalConfig {
  /** Segundos desde captured_at para considerar una posición desactualizada. */
  staleAfterSeconds: number;
  /** Segundos sin mensajes (ni heartbeat) para dar la conexión por perdida. */
  connectionTimeoutSeconds: number;
}

export const defaultConfig: PortalConfig = {
  staleAfterSeconds: 120,
  connectionTimeoutSeconds: 45,
};

/** nginx genera /config.json desde variables de entorno del contenedor. */
export async function loadConfig(): Promise<PortalConfig> {
  try {
    const resp = await fetch("/config.json", { cache: "no-store" });
    if (!resp.ok) return defaultConfig;
    const raw = (await resp.json()) as Partial<PortalConfig>;
    return {
      staleAfterSeconds: positive(raw.staleAfterSeconds, defaultConfig.staleAfterSeconds),
      connectionTimeoutSeconds: positive(
        raw.connectionTimeoutSeconds,
        defaultConfig.connectionTimeoutSeconds,
      ),
    };
  } catch {
    return defaultConfig;
  }
}

function positive(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : fallback;
}
