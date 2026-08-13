import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/*
 * Dev:  vite serves the app on :5173 and proxies /api to Flask on :5001, so the
 *       browser sees one origin and there's no CORS to configure.
 * Prod: `npm run build` emits frontend/dist, which Flask serves directly.
 *
 * Either way the app calls "/api/..." relative — no base URL to keep in sync.
 */
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:5001",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
