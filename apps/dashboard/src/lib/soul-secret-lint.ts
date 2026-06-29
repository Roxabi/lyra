/** Soft warnings for likely secrets embedded in soul markdown (operator lint). */

export type SoulSecretKind = "openai_key" | "github_token" | "bearer" | "pem";

export interface SoulSecretFinding {
  kind: SoulSecretKind;
  label: string;
}

const PATTERNS: ReadonlyArray<{ kind: SoulSecretKind; label: string; re: RegExp }> = [
  { kind: "openai_key", label: "OpenAI-style key (sk-…)", re: /\bsk-[A-Za-z0-9]{8,}\b/ },
  { kind: "github_token", label: "GitHub token (ghp_…)", re: /\bghp_[A-Za-z0-9]{20,}\b/ },
  { kind: "bearer", label: "Bearer token", re: /\bBearer\s+[A-Za-z0-9._-]{8,}\b/ },
  {
    kind: "pem",
    label: "PEM private key block",
    re: /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/,
  },
];

export function scanSoulMarkdownForSecrets(markdown: string): SoulSecretFinding[] {
  const findings: SoulSecretFinding[] = [];
  for (const { kind, label, re } of PATTERNS) {
    if (re.test(markdown)) {
      findings.push({ kind, label });
    }
  }
  return findings;
}

export function formatSoulSecretWarning(findings: SoulSecretFinding[]): string {
  if (findings.length === 0) return "";
  const labels = findings.map((f) => f.label).join(", ");
  return `Possible secret detected in soul text (${labels}). Remove before saving if unintended.`;
}
