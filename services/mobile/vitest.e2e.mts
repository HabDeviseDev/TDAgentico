import { defineConfig } from "vitest/config";

// Recorrido contra la API real (no forma parte de `npm test`): `npm run e2e`.
export default defineConfig({
  test: {
    include: ["e2e/**/*.e2e.ts"],
    environment: "node",
    testTimeout: 180_000,
  },
});
