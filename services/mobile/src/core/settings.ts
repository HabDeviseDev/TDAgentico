import type { SqlDb } from "./db";

export interface DriverSettings {
  apiUrl: string;
  vehicleId: string;
}

export const DEFAULT_SETTINGS: DriverSettings = {
  // Emulador de Android: 10.0.2.2 es el host. En un teléfono, la IP de la PC en la red local.
  apiUrl: "http://10.0.2.2:8000",
  vehicleId: "MOVIL-001",
};

/** Configuración guardada en SQLite (sobrevive al cierre de la app). */
export class SettingsStore {
  constructor(private readonly db: SqlDb) {}

  async load(): Promise<DriverSettings> {
    const rows = await this.db.all<{ key: string; value: string }>("SELECT key, value FROM settings");
    const map = new Map(rows.map((r) => [r.key, r.value]));
    return {
      apiUrl: map.get("apiUrl") ?? DEFAULT_SETTINGS.apiUrl,
      vehicleId: map.get("vehicleId") ?? DEFAULT_SETTINGS.vehicleId,
    };
  }

  async save(settings: DriverSettings): Promise<void> {
    await this.db.transaction(async (tx) => {
      for (const [key, value] of Object.entries(settings)) {
        await tx.run(
          "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
          [key, value],
        );
      }
    });
  }
}
