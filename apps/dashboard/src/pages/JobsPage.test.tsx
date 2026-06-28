import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "@/lib/api";
import { JobsPage } from "@/pages/JobsPage";

function renderJobs() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <JobsPage />
    </QueryClientProvider>,
  );
}

describe("JobsPage", () => {
  beforeEach(() => {
    vi.spyOn(api, "fetchAgents").mockResolvedValue(["lyra"]);
    vi.spyOn(api, "fetchAgentStatus").mockResolvedValue([
      {
        agent: "lyra",
        in_roster: true,
        harness: "claude-cli",
        harness_reachable: true,
        online: true,
      },
    ]);
    vi.spyOn(api, "fetchJobs").mockResolvedValue([
      {
        job_id: "job-abc",
        pool_id: "web:smoke:agent:lyra",
        agent: "lyra",
        platform: "web",
        status: "open",
        started_at: "2026-06-28T12:00:00+00:00",
        concurrency_mode: "steer",
        worker_loc: "clipool-worker",
        steer_subject: "factory.job.job-abc.steer",
      },
    ]);
  });

  it("renders live jobs table", async () => {
    renderJobs();
    await waitFor(() => {
      expect(screen.getByText("job-abc")).toBeTruthy();
    });
    expect(screen.getAllByText("Lyra").length).toBeGreaterThan(0);
    expect(screen.getByText("open")).toBeTruthy();
  });
});
