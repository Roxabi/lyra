import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vitest/config";

export default defineConfig({
  // Type-only bridge: `vitest/config` (vitest 3.x) types plugins against its
  // own vite@7 (rollup), while @vitejs/plugin-react v6 is typed against the
  // app's vite@8 (rolldown) — the hook signatures differ nominally but the
  // plugin is runtime-compatible (117 tests pass). Drop the cast when vitest
  // peers on vite 8.
  plugins: [react() as Plugin[]],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    deps: {
      optimizer: {
        // Prebundle the @phosphor-icons/react barrel once with esbuild instead
        // of re-executing it in every test file's module graph — setup time was
        // 102s of the 140s cumulative suite cost (73%) without it. `enabled`
        // is required: `include` alone is a no-op.
        web: {
          enabled: true,
          include: ["@phosphor-icons/react"],
        },
      },
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "./src"),
    },
  },
});
