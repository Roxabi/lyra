import type { TFunction } from "i18next";
import { describe, expect, it } from "vitest";
import { BffApiError } from "@/lib/bff-api";
import { bffErrorMessage } from "@/lib/bff-errors";

const t = ((key: string) => key) as TFunction;

describe("bffErrorMessage", () => {
  it("maps known conflict codes to i18n keys", () => {
    expect(bffErrorMessage(new BffApiError(409, "email already registered"), t, "create")).toBe(
      "errorEmailConflict",
    );
    expect(
      bffErrorMessage(new BffApiError(409, "platform identity already linked"), t, "edit"),
    ).toBe("errorPlatformConflict");
    expect(bffErrorMessage(new BffApiError(409, "unknown agent(s): x"), t, "create")).toBe(
      "errorUnknownAgent",
    );
    expect(bffErrorMessage(new BffApiError(409, "agent 'lyra' already exists"), t, "create")).toBe(
      "errorAgentConflict",
    );
    expect(bffErrorMessage(new BffApiError(404, "not_found"), t, "edit")).toBe("errorNotFound");
  });

  it("falls back to mode-specific generic errors", () => {
    expect(bffErrorMessage(new BffApiError(503, "store_unavailable"), t, "create")).toBe(
      "createError",
    );
    expect(bffErrorMessage(new BffApiError(503, "store_unavailable"), t, "edit")).toBe("editError");
  });

  it("falls back for non-BffApiError errors", () => {
    expect(bffErrorMessage(new Error("network"), t, "create")).toBe("createError");
    expect(bffErrorMessage(new Error("network"), t, "edit")).toBe("editError");
  });
});
