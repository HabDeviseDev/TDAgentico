import { defineConfig } from "vitest/config";

// Pruebas de la lógica de la app (sin React Native): contrato, cola SQLite,
// coordinador de sincronización. SQLite real vía node:sqlite (Node >= 22.13).
export default defineConfig({
  test: {
    include: ["test/**/*.test.ts"],
    environment: "node",
    pool: "forks",
    // Con SQLite en disco y CI compartidos, 5 s (por defecto) queda justo.
    testTimeout: 30_000,
  },
});
