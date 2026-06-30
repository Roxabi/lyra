import { describe, expect, it } from "vitest";
import { userDisplayName, userInitials } from "@/lib/user";

describe("user helpers", () => {
  it("derives display name and initials from name", () => {
    const user = { name: "Jane Doe", email: "jane@example.com" };
    expect(userDisplayName(user)).toBe("Jane Doe");
    expect(userInitials(user)).toBe("JD");
  });

  it("falls back to email when name is empty", () => {
    const user = { name: "  ", email: "ops@roxabi.dev" };
    expect(userDisplayName(user)).toBe("ops@roxabi.dev");
    expect(userInitials(user)).toBe("OP");
  });
});