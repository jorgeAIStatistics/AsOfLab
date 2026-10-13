import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The API is the local, read-only FastAPI app started with `asoflab serve`.
// In development Vite proxies /api to it; in production the same app serves
// web/dist at /, so the UI always uses relative /api/... URLs.
const API = "http://127.0.0.1:8010";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": { target: API, changeOrigin: false } },
  },
  preview: {
    proxy: { "/api": { target: API, changeOrigin: false } },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
  },
});
