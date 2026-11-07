/**
 * SQLite REAL para las pruebas (módulo `node:sqlite` de Node 22), con la
 * misma interfaz que el adaptador de expo-sqlite. Usa archivos en disco para
 * comprobar que la cola sobrevive a cerrar y volver a abrir la base.
 */
import { DatabaseSync } from "node:sqlite";
import { lockedDb, type SqlDb, type SqlExecutor, type SqlValue } from "../src/core/db";

export function openNodeDb(path: string): SqlDb {
  const db = new DatabaseSync(path);
  db.exec("PRAGMA journal_mode = WAL; PRAGMA foreign_keys = ON;");
  const raw: SqlExecutor = {
    async run(sql: string, params: SqlValue[] = []) {
      const r = db.prepare(sql).run(...params);
      return { changes: Number(r.changes), lastInsertRowId: Number(r.lastInsertRowid) };
    },
    async all<T>(sql: string, params: SqlValue[] = []) {
      return db.prepare(sql).all(...params) as T[];
    },
    async get<T>(sql: string, params: SqlValue[] = []) {
      return (db.prepare(sql).get(...params) as T | undefined) ?? null;
    },
    async exec(sql: string) {
      db.exec(sql);
    },
  };
  return lockedDb(raw, async () => db.close());
}
