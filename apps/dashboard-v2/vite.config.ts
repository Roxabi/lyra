import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { dashboardDevMockPlugin } from "./dev-mock/plugin";

const repoRoot = path.resolve(import.meta.dirname, "../..");
const useMock = process.env.DASHBOARD_MOCK !== "0";

export default defineConfig({
  plugins: [react(), tailwindcss(), dashboardDevMockPlugin()],
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "./src"),
    },
  },
  server: {
    host: "127.0.0.1",
    port: 5176,
    strictPort: true,
    fs: {
      allow: [repoRoot],
    },
    proxy: useMock
      ? undefined
      : {
          "/api": {
            target: "http://127.0.0.1:8765",
            changeOrigin: true,
          },
        },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
