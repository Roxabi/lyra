import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AgentStatusBadge } from "@/components/AgentStatusBadge";
import type { AgentHealth } from "@/lib/api";
import "@/i18n";

const base: AgentHealth = {
  agent: "lyra",
  in_roster: true,
  harness: "claude-cli",
  harness_reachable: true,
  online: true,
};

describe("AgentStatusBadge", () => {
  it("renders nothing without health", () => {
    const { container } = render(<AgentStatusBadge health={undefined} />);
    expect(container.firstChild).toBeNull();
  });

  it("shows the online label when online", () => {
    render(<AgentStatusBadge health={{ ...base, online: true }} />);
    expect(screen.getByText("En ligne")).toBeTruthy();
  });

  it("shows the offline label when offline", () => {
    render(<AgentStatusBadge health={{ ...base, online: false }} />);
    expect(screen.getByText("Hors ligne")).toBeTruthy();
  });
});
