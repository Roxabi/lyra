import type { TFunction } from "i18next";

export type ChatErrorCode =
  | "adapter_not_ready"
  | "send_failed"
  | "agents_fetch_failed"
  | "status_fetch_failed"
  | "sessions_fetch_failed"
  | "turns_fetch_failed"
  | "resume_failed"
  | "stream_failed";

export class ChatApiError extends Error {
  readonly code: ChatErrorCode;

  constructor(code: ChatErrorCode) {
    super(code);
    this.name = "ChatApiError";
    this.code = code;
  }
}

export function chatErrorMessage(err: unknown, t: TFunction): string {
  if (err instanceof ChatApiError) {
    switch (err.code) {
      case "adapter_not_ready":
        return t("errors.adapterNotReady");
      case "send_failed":
        return t("errors.sendFailed");
      case "agents_fetch_failed":
        return t("errors.agentsFetchFailed");
      case "status_fetch_failed":
        return t("errors.statusFetchFailed");
      case "sessions_fetch_failed":
        return t("errors.sessionsFetchFailed");
      case "turns_fetch_failed":
        return t("errors.turnsFetchFailed");
      case "resume_failed":
        return t("errors.resumeFailed");
      case "stream_failed":
        return t("errors.streamFailed");
    }
  }
  if (err instanceof Error && err.message) return err.message;
  return t("errors.sendFailed");
}
