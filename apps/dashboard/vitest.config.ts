import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    // Single retry under CI only: absorbs shared-runner infra flake without
    // masking real failures locally (retry stays 0 → identical local behavior).
    retry: process.env.CI ? 1 : 0,
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
