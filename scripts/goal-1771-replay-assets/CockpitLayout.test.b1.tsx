import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { CockpitLayout } from "@/components/CockpitLayout";
import * as api from "@/lib/api";

vi.spyOn(api, "fetchAgents").mockResolvedValue(["lyra"]);

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("CockpitLayout", () => {
  it("renders multi-chat shell without Reprendre panel (Block 1)", async () => {
    render(<CockpitLayout />, { wrapper });
    expect(await screen.findByText("lyra")).toBeTruthy();
    expect(screen.queryByText("Reprendre")).toBeNull();
  });
});