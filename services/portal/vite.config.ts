import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// En desarrollo (`npm run dev`) se replica el proxy de nginx: REST y SSE
// desde el mismo origen del portal.
const apiTarget = process.env.API_PROXY_TARGET ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/v1": { target: apiTarget, changeOrigin: true },
    },
  },
  test: {
    environment: "node",
  },
});
