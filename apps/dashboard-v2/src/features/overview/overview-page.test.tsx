import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as overviewApi from "@/features/overview/api";
import { OverviewPage } from "@/features/overview/overview-page";
import i18n from "@/i18n";
import { renderWithRouter } from "@/test-utils/router";

describe("OverviewPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(overviewApi, "fetchAgents").mockResolvedValue(["lyra"]);
    vi.spyOn(overviewApi, "fetchAgentStatus").mockResolvedValue([
      {
        agent: "lyra",
        in_roster: true,
        harness: "claude-cli",
        harness_reachable: true,
        online: true,
      },
    ]);
    vi.spyOn(overviewApi, "fetchJobs").mockResolvedValue([]);
    vi.spyOn(overviewApi, "fetchOpsHealth").mockResolvedValue([
      { engine: "loki", label: "Loki", reachable: true, detail: "ok" },
    ]);
  });

  it("shows agent roster error when status fetch fails", async () => {
    vi.spyOn(overviewApi, "fetchAgentStatus").mockRejectedValue(new Error("status down"));
    const { ui } = renderWithRouter(<OverviewPage />);
    render(ui);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(i18n.t("dashboard:agents.loadError"));
  });

  it("shows jobs error when jobs fetch fails", async () => {
    vi.spyOn(overviewApi, "fetchJobs").mockRejectedValue(new Error("jobs down"));
    const { ui } = renderWithRouter(<OverviewPage />);
    render(ui);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(i18n.t("dashboard:jobs.loadError"));
  });
});
