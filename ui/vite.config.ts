import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The SPA is served by the gateway itself at /ui (see gateway/api.py), so the
// production base is /ui/ and all API calls are same-origin relative paths.
// `npm run dev` proxies those paths to a locally running gateway (start one
// with OT2_DRY_RUN=true for a no-hardware loop).
export default defineConfig({
  base: "/ui/",
  plugins: [react(), tailwindcss()],
  build: {
    outDir: "../src/opentrons_server/ui_dist",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/status": "http://127.0.0.1:8020",
      "/health": "http://127.0.0.1:8020",
      "/control": "http://127.0.0.1:8020",
      "/labware": "http://127.0.0.1:8020",
    },
  },
});
