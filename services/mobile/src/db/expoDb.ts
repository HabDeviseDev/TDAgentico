import * as SQLite from "expo-sqlite";
import { lockedDb, type SqlDb, type SqlExecutor, type SqlValue } from "../core/db";

/** SQLite del teléfono (expo-sqlite) con la interfaz de la cola. */
export async function openExpoDb(name = "driver.db"): Promise<SqlDb> {
  const db = await SQLite.openDatabaseAsync(name);
  await db.execAsync("PRAGMA journal_mode = WAL; PRAGMA foreign_keys = ON;");
  const raw: SqlExecutor = {
    async run(sql: string, params: SqlValue[] = []) {
      const r = await db.runAsync(sql, params);
      return { changes: r.changes, lastInsertRowId: r.lastInsertRowId };
    },
    all: <T,>(sql: string, params: SqlValue[] = []) => db.getAllAsync<T>(sql, params),
    get: <T,>(sql: string, params: SqlValue[] = []) => db.getFirstAsync<T>(sql, params),
    exec: (sql: string) => db.execAsync(sql),
  };
  return lockedDb(raw, () => db.closeAsync());
}
