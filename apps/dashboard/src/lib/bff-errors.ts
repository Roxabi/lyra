import type { TFunction } from "i18next";
import { BffApiError } from "@/lib/bff-api";

type FormMode = "create" | "edit";

export function bffErrorMessage(err: unknown, t: TFunction, mode: FormMode): string {
  if (!(err instanceof BffApiError)) {
    return t(mode === "edit" ? "editError" : "createError");
  }

  switch (err.code) {
    case "email_conflict":
      return t("errorEmailConflict");
    case "platform_conflict":
      return t("errorPlatformConflict");
    case "unknown_agent":
      return t("errorUnknownAgent");
    case "agent_conflict":
      return t("errorAgentConflict");
    case "not_found":
      return t("errorNotFound");
    default:
      return t(mode === "edit" ? "editError" : "createError");
  }
}
