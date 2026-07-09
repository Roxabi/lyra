import { BffApiError } from "@/shared/api/client";

const CODE_MESSAGES: Record<string, string> = {
  email_conflict: "Email already registered.",
  platform_conflict: "Platform identity already linked.",
  unknown_agent: "Unknown agent.",
  agent_conflict: "Agent already exists.",
  not_found: "Not found.",
};

export function bffErrorMessage(err: unknown, fallback = "Request failed."): string {
  if (err instanceof BffApiError) {
    return CODE_MESSAGES[err.code] ?? err.detail ?? fallback;
  }
  return fallback;
}
