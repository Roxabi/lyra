export type BffErrorCode =
  | "email_conflict"
  | "platform_conflict"
  | "agent_conflict"
  | "unknown_agent"
  | "not_found"
  | "generic";

export class BffApiError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly code: BffErrorCode;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "BffApiError";
    this.status = status;
    this.detail = detail;
    this.code = classifyBffDetail(detail);
  }
}

export function classifyBffDetail(detail: string): BffErrorCode {
  const normalized = detail.trim().toLowerCase();
  if (!normalized) return "generic";
  if (normalized.includes("email already registered")) return "email_conflict";
  if (normalized.includes("platform identity already linked")) return "platform_conflict";
  if (normalized.includes("unknown agent")) return "unknown_agent";
  if (normalized.includes("already exists")) return "agent_conflict";
  if (normalized.includes("not_found") || normalized.includes("not found")) return "not_found";
  return "generic";
}

export async function readBffErrorDetail(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    if (typeof body.detail === "string" && body.detail.trim()) {
      return body.detail.trim();
    }
  } catch {
    // non-JSON or empty body
  }
  return `request failed (${res.status})`;
}

export async function parseBffResponse<T>(res: Response): Promise<T> {
  if (res.ok) {
    return res.json() as Promise<T>;
  }
  const detail = await readBffErrorDetail(res);
  throw new BffApiError(res.status, detail);
}