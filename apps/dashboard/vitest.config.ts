import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    // Single retry under CI only — a SCOPED EXCEPTION to the repo's
    // no-unit-retry doctrine (see ci.yml doctrine comments): the failure class
    // absorbed here is jsdom/react-testing-library scheduling flake on shared
    // runners, and attempts share no state (fresh module graph per retry).
    // Accepted trade: this suite is junit-less, so a retry-masked flake is
    // invisible — revisit if dashboard tests grow stateful or a flake budget
    // is needed. Strict "true" check: CI="false" must not enable the retry.
    retry: process.env.CI === "true" ? 1 : 0,
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
