import { defineConfig } from "vite";

// Dev-server proxy: the browser sees a single origin, so no CORS changes are
// needed on the integration API (invariant 11 — the frontend consumes the
// public contract untouched). Target is env-driven (invariant 7).
export default defineConfig({
  server: {
    proxy: {
      "/v1": {
        target: process.env.API_PROXY_TARGET || "http://integration-api:8000",
        changeOrigin: true,
      },
    },
  },
});
