import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import {
  BackendAvailabilityProvider,
  useBackendAvailability,
  AVAILABILITY_CONFIG,
} from "./backend-availability-context";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as api from "@/lib/api";

function TestConsumer({
  onState,
}: {
  onState?: (state: ReturnType<typeof useBackendAvailability>) => void;
}) {
  const state = useBackendAvailability();
  if (onState) onState(state);

  return (
    <div>
      <span data-testid="status">{state.status}</span>
      <span data-testid="elapsed">{state.elapsedSeconds}</span>
      <span data-testid="retryCount">{state.retryCount}</span>
      <span data-testid="isReady">{state.isReady ? "yes" : "no"}</span>
      <span data-testid="isWaking">{state.isWaking ? "yes" : "no"}</span>
      <span data-testid="isOffline">{state.isOffline ? "yes" : "no"}</span>
      <span data-testid="isDegraded">{state.isDegraded ? "yes" : "no"}</span>
      <button data-testid="retry-btn" onClick={() => state.retry()}>
        Retry
      </button>
      <button
        data-testid="check-btn"
        onClick={() => state.checkAvailability(true)}
      >
        Check
      </button>
    </div>
  );
}

function renderWithProviders(queryClient?: QueryClient, onState?: any) {
  const client =
    queryClient ||
    new QueryClient({
      defaultOptions: {
        queries: { retry: false },
      },
    });

  return render(
    <QueryClientProvider client={client}>
      <BackendAvailabilityProvider>
        <TestConsumer onState={onState} />
      </BackendAvailabilityProvider>
    </QueryClientProvider>
  );
}

describe("BackendAvailabilityContext & Manager", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("transitions from checking to ready when /health and /ready succeed immediately", async () => {
    vi.spyOn(api, "fetchHealth").mockResolvedValue({
      status: "ok",
      service: "api",
      version: "0.1.0",
      environment: "production",
    });
    vi.spyOn(api, "fetchReady").mockResolvedValue({
      status: "ready",
      database: "connected",
      environment: "production",
    });

    renderWithProviders();

    expect(screen.getByTestId("status").textContent).toBe("checking");

    // Resolve promises
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });

    expect(screen.getByTestId("status").textContent).toBe("ready");
    expect(screen.getByTestId("isReady").textContent).toBe("yes");
    expect(screen.getByTestId("isWaking").textContent).toBe("no");
    expect(screen.getByTestId("isOffline").textContent).toBe("no");
  });

  it("transitions to waking when initial probe is slow (> 2s) and recovers on retry", async () => {
    let callCount = 0;
    vi.spyOn(api, "fetchHealth").mockImplementation(async () => {
      callCount++;
      if (callCount === 1) {
        // Cold start delay
        await new Promise((resolve) => setTimeout(resolve, 3000));
        throw new Error("Render container spinning up (502 Bad Gateway)");
      }
      return {
        status: "ok",
        service: "api",
        version: "0.1.0",
        environment: "production",
      };
    });

    vi.spyOn(api, "fetchReady").mockResolvedValue({
      status: "ready",
      database: "connected",
      environment: "production",
    });

    renderWithProviders();

    // Fast-waking threshold timer is 2000ms
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2100);
    });

    expect(screen.getByTestId("status").textContent).toBe("waking");
    expect(screen.getByTestId("isWaking").textContent).toBe("yes");

    // Advance past probe completion (3000ms) and retry backoff delay (~3500ms)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(8000);
    });

    expect(screen.getByTestId("status").textContent).toBe("ready");
    expect(screen.getByTestId("isReady").textContent).toBe("yes");
  });

  it("transitions to degraded when /health is ok but /ready returns 503 database unavailable", async () => {
    vi.spyOn(api, "fetchHealth").mockResolvedValue({
      status: "ok",
      service: "api",
      version: "0.1.0",
      environment: "production",
    });

    vi.spyOn(api, "fetchReady").mockRejectedValue({
      response: {
        status: 503,
        data: { status: "not_ready", database: "disconnected" },
      },
    });

    renderWithProviders();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(200);
    });

    expect(screen.getByTestId("status").textContent).toBe("degraded");
    expect(screen.getByTestId("isDegraded").textContent).toBe("yes");
  });

  it("transitions to offline when total cold start deadline (120s) is exceeded", async () => {
    vi.spyOn(api, "fetchHealth").mockRejectedValue(
      new Error("Network Error (Connection Refused)")
    );

    renderWithProviders();

    // Advance past total deadline (120 seconds) in retry increments
    for (let i = 0; i < 25; i++) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(6000);
      });
    }

    expect(screen.getByTestId("status").textContent).toBe("offline");
    expect(screen.getByTestId("isOffline").textContent).toBe("yes");
    expect(screen.getByTestId("isWaking").textContent).toBe("no");
  });

  it("deduplicates concurrent checkAvailability requests to prevent duplicate network flights", async () => {
    let healthCalls = 0;
    vi.spyOn(api, "fetchHealth").mockImplementation(async () => {
      healthCalls++;
      await new Promise((resolve) => setTimeout(resolve, 500));
      return {
        status: "ok",
        service: "api",
        version: "0.1.0",
        environment: "production",
      };
    });

    vi.spyOn(api, "fetchReady").mockResolvedValue({
      status: "ready",
      database: "connected",
      environment: "production",
    });

    let exposedState: ReturnType<typeof useBackendAvailability> | undefined;
    renderWithProviders(undefined, (s: ReturnType<typeof useBackendAvailability>) => {
      exposedState = s;
    });

    // Fire 5 rapid simultaneous checks without forcing fresh flight
    await act(async () => {
      const p1 = exposedState?.checkAvailability(false);
      const p2 = exposedState?.checkAvailability(false);
      const p3 = exposedState?.checkAvailability(false);
      const p4 = exposedState?.checkAvailability(false);
      const p5 = exposedState?.checkAvailability(false);

      // Verify they share the exact same promise instance
      expect(p1).toBe(p2);
      expect(p2).toBe(p3);
      expect(p3).toBe(p4);
      expect(p4).toBe(p5);

      await vi.advanceTimersByTimeAsync(600);
    });

    // Only 1 health call was made because all were deduplicated into the single inflight flight
    expect(healthCalls).toBe(1);
    expect(screen.getByTestId("status").textContent).toBe("ready");
  });

  it("invalidates queries (documents, conversations) upon transitioning to ready", async () => {
    vi.spyOn(api, "fetchHealth").mockResolvedValue({
      status: "ok",
      service: "api",
      version: "0.1.0",
      environment: "production",
    });
    vi.spyOn(api, "fetchReady").mockResolvedValue({
      status: "ready",
      database: "connected",
      environment: "production",
    });

    const queryClient = new QueryClient();
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    renderWithProviders(queryClient);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(200);
    });

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["documents"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["conversations"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["health"] });
  });

  it("aborts outdated requests when a manual retry is clicked", async () => {
    let aborted = false;
    vi.spyOn(api, "fetchHealth").mockImplementation(async (signal?: AbortSignal) => {
      if (signal) {
        signal.addEventListener("abort", () => {
          aborted = true;
        });
      }
      await new Promise((resolve) => setTimeout(resolve, 5000));
      return {
        status: "ok",
        service: "api",
        version: "0.1.0",
        environment: "production",
      };
    });

    renderWithProviders();

    // Click retry before the first 5000ms probe finishes
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
      screen.getByTestId("retry-btn").click();
    });

    expect(aborted).toBe(true);
  });
});
