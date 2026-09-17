import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The war room talks to the FastAPI backend on :8080. We proxy /api (including
// the SSE stream) so the dev server and prod build hit identical relative URLs.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8080",
        changeOrigin: true,
        // SSE must not be buffered by the proxy or events arrive in bursts.
        configure: (proxy) => {
          proxy.on("proxyReq", (proxyReq) => {
            proxyReq.setHeader("Accept-Encoding", "identity");
          });
        },
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
