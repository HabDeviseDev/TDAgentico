/**
 * Interfaz mínima de SQLite usada por la cola. La implementan:
 * - `src/db/expoDb.ts` (expo-sqlite, en el teléfono);
 * - `test/nodeDb.ts` (node:sqlite, SQLite real en las pruebas).
 *
 * Todas las operaciones pasan por un candado: una transacción tiene la base
 * para ella sola hasta terminar (sin intercalar escrituras de la captura).
 */

export type SqlValue = string | number | null;

export interface SqlExecutor {
  run(sql: string, params?: SqlValue[]): Promise<{ changes: number; lastInsertRowId: number }>;
  all<T>(sql: string, params?: SqlValue[]): Promise<T[]>;
  get<T>(sql: string, params?: SqlValue[]): Promise<T | null>;
  exec(sql: string): Promise<void>;
}

export interface SqlDb extends SqlExecutor {
  /** BEGIN IMMEDIATE … COMMIT; ROLLBACK si `fn` falla. */
  transaction<T>(fn: (tx: SqlExecutor) => Promise<T>): Promise<T>;
  close(): Promise<void>;
}

/** Candado asíncrono FIFO. */
export class Mutex {
  private tail: Promise<void> = Promise.resolve();

  async lock<T>(fn: () => Promise<T>): Promise<T> {
    const previous = this.tail;
    let release!: () => void;
    this.tail = new Promise<void>((resolve) => (release = resolve));
    await previous;
    try {
      return await fn();
    } finally {
      release();
    }
  }
}

/** Envuelve un ejecutor sin candado con candado y transacciones. */
export function lockedDb(raw: SqlExecutor, close: () => Promise<void>): SqlDb {
  const mutex = new Mutex();
  return {
    run: (sql, params) => mutex.lock(() => raw.run(sql, params)),
    all: (sql, params) => mutex.lock(() => raw.all(sql, params)),
    get: (sql, params) => mutex.lock(() => raw.get(sql, params)),
    exec: (sql) => mutex.lock(() => raw.exec(sql)),
    transaction: (fn) =>
      mutex.lock(async () => {
        await raw.exec("BEGIN IMMEDIATE");
        try {
          const result = await fn(raw);
          await raw.exec("COMMIT");
          return result;
        } catch (err) {
          await raw.exec("ROLLBACK");
          throw err;
        }
      }),
    close: () => mutex.lock(close),
  };
}
