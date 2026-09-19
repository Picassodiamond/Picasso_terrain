import { defineConfig } from "vite";
import cesium from "vite-plugin-cesium";

// Dev: `npm run dev` proxies /api to the FastAPI dev server (uvicorn on :8000).
// Prod: `npm run build` -> dist/ served by FastAPI (PLM_WEB_DIST) or Apache.
export default defineConfig({
  plugins: [cesium()],
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: false } },
  },
  build: {
    target: "es2020",
    sourcemap: false,
    chunkSizeWarningLimit: 4000,
  },
});
