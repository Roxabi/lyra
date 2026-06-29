import { describe, expect, it } from "vitest";
import { formatSoulSecretWarning, scanSoulMarkdownForSecrets } from "@/lib/soul-secret-lint";

describe("soul-secret-lint", () => {
  it("detects OpenAI and GitHub token patterns", () => {
    const md = "Use sk-abcdefghijklmnopqrstuvwx and ghp_1234567890123456789012345678901234";
    const findings = scanSoulMarkdownForSecrets(md);
    expect(findings.map((f) => f.kind)).toContain("openai_key");
    expect(findings.map((f) => f.kind)).toContain("github_token");
    expect(formatSoulSecretWarning(findings)).toMatch(/Possible secret/);
  });

  it("detects Bearer and PEM patterns", () => {
    const md = "Auth: Bearer eyJhbGciOiJIUzI1NiJ9.abc\n-----BEGIN RSA PRIVATE KEY-----";
    const findings = scanSoulMarkdownForSecrets(md);
    expect(findings.map((f) => f.kind)).toContain("bearer");
    expect(findings.map((f) => f.kind)).toContain("pem");
  });

  it("returns empty for benign soul text", () => {
    expect(scanSoulMarkdownForSecrets("## Identity\nI am Lyra.")).toEqual([]);
    expect(formatSoulSecretWarning([])).toBe("");
  });
});
