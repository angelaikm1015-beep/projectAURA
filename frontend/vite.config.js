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
        // Generating a score takes the model a minute or two, and a score that
        // needs correcting costs a second round on top. The proxy's default
        // timeout would cut the request off long before Flask answers.
        timeout: 600000,
        proxyTimeout: 600000,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
