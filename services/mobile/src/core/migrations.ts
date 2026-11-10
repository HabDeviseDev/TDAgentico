import type { SqlDb } from "./db";

/**
 * Migraciones locales reproducibles. La versión aplicada se guarda en
 * `PRAGMA user_version`; cada migración corre en su propia transacción
 * junto con el cambio de versión. Solo se agregan migraciones nuevas al final.
 */
export const MIGRATIONS: { version: number; name: string; sql: string }[] = [
  {
    version: 1,
    name: "cola de posiciones",
    sql: `
      CREATE TABLE outbox (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id        TEXT    NOT NULL UNIQUE,      -- se genera una sola vez
        vehicle_id      TEXT    NOT NULL,
        captured_at     TEXT,                         -- ISO-8601 UTC ("Z")
        lat             REAL,
        lon             REAL,
        speed_kmh       REAL,                         -- NULL: velocidad desconocida
        payload         TEXT,                         -- JSON exacto que se envía y reenvía
        simulated       INTEGER NOT NULL DEFAULT 0,
        status          TEXT    NOT NULL
                        CHECK (status IN ('pending', 'sending', 'accepted', 'rejected', 'skipped')),
        attempts        INTEGER NOT NULL DEFAULT 0,
        next_attempt_at INTEGER NOT NULL DEFAULT 0,   -- epoch ms
        last_error      TEXT,
        created_at      INTEGER NOT NULL,
        updated_at      INTEGER NOT NULL,
        accepted_at     INTEGER,
        CHECK ((status = 'skipped') = (payload IS NULL))
      );
      CREATE INDEX outbox_due ON outbox (status, next_attempt_at, id);
    `,
  },
  {
    version: 2,
    name: "configuración local",
    sql: `
      CREATE TABLE settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
      );
    `,
  },
];

export async function migrate(db: SqlDb): Promise<number> {
  const row = await db.get<{ user_version: number }>("PRAGMA user_version");
  let current = row?.user_version ?? 0;
  for (const m of MIGRATIONS) {
    if (m.version <= current) continue;
    await db.transaction(async (tx) => {
      await tx.exec(m.sql);
      await tx.exec(`PRAGMA user_version = ${m.version}`);
    });
    current = m.version;
  }
  return current;
}
