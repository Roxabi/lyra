import { describe, expect, it } from "vitest";
import { formatSoulSecretWarning, scanSoulMarkdownForSecrets } from "@/shared/lib/soul-secret-lint";

describe("soul-secret-lint", () => {
  it("detects OpenAI keys", () => {
    const findings = scanSoulMarkdownForSecrets("Use sk-abcdefghijklmnopqrstuvwxyz");
    expect(findings.some((f) => f.kind === "openai_key")).toBe(true);
  });

  it("returns empty for clean markdown", () => {
    expect(scanSoulMarkdownForSecrets("No secrets here.")).toEqual([]);
    expect(formatSoulSecretWarning([])).toBe("");
  });

  it("formats warning message", () => {
    const findings = scanSoulMarkdownForSecrets("Bearer abcdefghijklmnop");
    expect(formatSoulSecretWarning(findings)).toContain("Bearer token");
  });
});
