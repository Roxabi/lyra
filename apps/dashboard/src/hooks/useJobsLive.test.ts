import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useJobsLive } from "@/hooks/useJobsLive";
import * as api from "@/lib/api";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useJobsLive", () => {
  it("surfaces isError on an error frame", async () => {
    vi.spyOn(api, "postJobsStreamToken").mockResolvedValue({ stream_token: "t" });
    vi.spyOn(api, "openJobsStream").mockImplementation((_token, onEvent) => {
      onEvent({ type: "error", message: "boom" });
      return { close: vi.fn() } as unknown as EventSource;
    });

    const { result } = renderHook(() => useJobsLive());

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.isLoading).toBe(false);
  });

  it("surfaces isError when the token fetch rejects", async () => {
    vi.spyOn(api, "postJobsStreamToken").mockRejectedValue(new Error("no token"));
    const openSpy = vi.spyOn(api, "openJobsStream");

    const { result } = renderHook(() => useJobsLive());

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(openSpy).not.toHaveBeenCalled();
  });

  it("closes the EventSource on unmount", async () => {
    const close = vi.fn();
    vi.spyOn(api, "postJobsStreamToken").mockResolvedValue({ stream_token: "t" });
    vi.spyOn(api, "openJobsStream").mockImplementation((_token, onEvent) => {
      onEvent({ type: "snapshot", jobs: [] });
      return { close } as unknown as EventSource;
    });

    const { unmount } = renderHook(() => useJobsLive());
    await waitFor(() => expect(api.openJobsStream).toHaveBeenCalled());

    unmount();
    expect(close).toHaveBeenCalled();
  });
});
