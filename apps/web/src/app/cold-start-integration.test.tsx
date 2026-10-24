import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BackendAvailabilityProvider } from "@/context/backend-availability-context";
import { AuthProvider } from "@/context/auth-context";
import Home from "./page";
import * as api from "@/lib/api";

function renderAppWithProviders() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <BackendAvailabilityProvider>
        <AuthProvider>
          <Home />
        </AuthProvider>
      </BackendAvailabilityProvider>
    </QueryClientProvider>
  );
}

describe("End-to-End Cold-Start Recovery Lifecycle", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    localStorage.clear();
    vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("opens homepage when backend is asleep, shows waking state without flashing offline, and recovers automatically without manual refresh", async () => {
    let healthAttempts = 0;
    vi.spyOn(api, "fetchHealth").mockImplementation(async () => {
      healthAttempts++;
      if (healthAttempts === 1) {
        // Cold start delay
        await new Promise((resolve) => setTimeout(resolve, 2500));
        throw new Error("Render service sleeping (502 Bad Gateway)");
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

    const mockDocs: api.DocumentListResponse = {
      total: 2,
      documents: [
        {
          id: "doc-1",
          user_id: "user-1",
          filename: "Architecture.pdf",
          storage_key: "docs/1.pdf",
          file_url: "https://storage/1.pdf",
          file_size: 1024 * 50,
          mime_type: "application/pdf",
          status: "indexed",
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        },
        {
          id: "doc-2",
          user_id: "user-1",
          filename: "Report.pdf",
          storage_key: "docs/2.pdf",
          file_url: "https://storage/2.pdf",
          file_size: 1024 * 80,
          mime_type: "application/pdf",
          status: "indexed",
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        },
      ],
    };

    let docFetchCalls = 0;
    vi.spyOn(api, "fetchDocuments").mockImplementation(async () => {
      docFetchCalls++;
      if (healthAttempts === 1) {
        throw new Error("Network Error");
      }
      return mockDocs;
    });

    // Mock existing user token in localStorage
    localStorage.setItem("search_sphere_token", "existing-valid-jwt");
    vi.spyOn(api, "fetchMe").mockImplementation(async () => {
      if (healthAttempts === 1) {
        throw new Error("Network Error during cold start");
      }
      return {
        id: "user-1",
        email: "alice@example.com",
        name: "Alice",
        avatar_url: null,
        auth_provider: "local",
        is_active: true,
        created_at: new Date().toISOString(),
      };
    });

    renderAppWithProviders();

    // Verify initial connecting status is rendered
    expect(screen.getByText("Connecting...")).toBeDefined();

    // Advance 2100ms: fast waking transition triggers
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2100);
    });

    // Verify UI reflects waking up status, NOT an alarming offline error
    expect(screen.getByText(/Waking up/i)).toBeDefined();
    expect(screen.queryByText("Unavailable")).toBeNull();

    // Verify stored JWT was NOT deleted from localStorage during the cold-start failure
    expect(localStorage.getItem("search_sphere_token")).toBe("existing-valid-jwt");

    // Advance time past the cold-start duration so attempt 2 succeeds (~7s total)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(8000);
    });

    // UI should now automatically show Connected without requiring manual browser refresh
    expect(screen.getByText("Connected")).toBeDefined();

    // User session should be restored automatically upon readiness
    expect(screen.getByText("Alice")).toBeDefined();
    expect(screen.getByText("alice@example.com")).toBeDefined();

    // Stored documents should have been automatically fetched upon ready transition
    expect(screen.getByText(/Stored Documents \(2\)/i)).toBeDefined();
    expect(screen.getByText("Architecture.pdf")).toBeDefined();
    expect(screen.getByText("Report.pdf")).toBeDefined();
  });

  it("protects document upload during cold start by preventing premature non-idempotent submissions", async () => {
    vi.spyOn(api, "fetchHealth").mockImplementation(async () => {
      await new Promise((resolve) => setTimeout(resolve, 3000));
      throw new Error("Render service sleeping");
    });

    localStorage.setItem("search_sphere_token", "jwt-token");
    vi.spyOn(api, "fetchMe").mockResolvedValue({
      id: "u-1",
      email: "bob@example.com",
      name: "Bob",
      avatar_url: null,
      auth_provider: "local",
      is_active: true,
      created_at: new Date().toISOString(),
    });

    const uploadSpy = vi.spyOn(api, "uploadDocument");

    renderAppWithProviders();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2200);
    });

    // While waking, the upload badge and dropzone reflect starting state
    expect(screen.getByText(/Waking Backend.../i)).toBeDefined();
    expect(
      screen.getByText(
        "Starting Search Sphere backend. This may take about a minute..."
      )
    ).toBeDefined();

    // uploadDocument must not be invoked while backend is dead or waking
    expect(uploadSpy).not.toHaveBeenCalled();
  });
});
