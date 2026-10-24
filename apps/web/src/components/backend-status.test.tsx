import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { BackendStatusIndicator } from "./backend-status-indicator";
import { BackendStatusBanner } from "./backend-status-banner";
import * as context from "@/context/backend-availability-context";

describe("Backend Status Components", () => {
  const mockContext = (overrides: Partial<context.BackendAvailabilityContextType> = {}) => {
    const defaultState: context.BackendAvailabilityContextType = {
      status: "ready",
      isReady: true,
      isWaking: false,
      isChecking: false,
      isDegraded: false,
      isOffline: false,
      elapsedSeconds: 0,
      retryCount: 0,
      lastChecked: Date.now(),
      errorMessage: null,
      checkAvailability: vi.fn().mockResolvedValue(true),
      retry: vi.fn().mockResolvedValue(true),
      ...overrides,
    };

    vi.spyOn(context, "useBackendAvailability").mockReturnValue(defaultState);
    return defaultState;
  };

  describe("BackendStatusIndicator", () => {
    it("renders Connecting... state with role=status and aria-live=polite", () => {
      mockContext({
        status: "checking",
        isReady: false,
        isChecking: true,
      });

      render(<BackendStatusIndicator />);

      const indicator = screen.getByRole("status");
      expect(indicator).toBeDefined();
      expect(indicator.getAttribute("aria-live")).toBe("polite");
      expect(screen.getByText("Connecting...")).toBeDefined();
    });

    it("renders Waking up with elapsed seconds without flashing offline in red", () => {
      mockContext({
        status: "waking",
        isReady: false,
        isWaking: true,
        elapsedSeconds: 24,
      });

      render(<BackendStatusIndicator />);

      expect(screen.getByText(/Waking up \(24s\)/i)).toBeDefined();
      expect(screen.queryByText(/Offline/i)).toBeNull();
      expect(screen.queryByText(/Unavailable/i)).toBeNull();
    });

    it("renders Connected state when backend is ready", () => {
      mockContext({
        status: "ready",
        isReady: true,
      });

      render(<BackendStatusIndicator />);

      expect(screen.getByText("Connected")).toBeDefined();
    });

    it("renders Degraded state with a working Retry button", () => {
      const mockState = mockContext({
        status: "degraded",
        isReady: false,
        isDegraded: true,
        errorMessage: "Database connectivity unavailable.",
      });

      render(<BackendStatusIndicator />);

      expect(screen.getByText("Degraded")).toBeDefined();
      const retryBtn = screen.getByRole("button", { name: /retry/i });
      fireEvent.click(retryBtn);
      expect(mockState.retry).toHaveBeenCalledTimes(1);
    });

    it("renders Unavailable state with Retry button when offline", () => {
      const mockState = mockContext({
        status: "offline",
        isReady: false,
        isOffline: true,
      });

      render(<BackendStatusIndicator />);

      expect(screen.getByText("Unavailable")).toBeDefined();
      const retryBtn = screen.getByRole("button", { name: /retry/i });
      fireEvent.click(retryBtn);
      expect(mockState.retry).toHaveBeenCalledTimes(1);
    });
  });

  describe("BackendStatusBanner", () => {
    it("renders waking message and elapsed time during waking", () => {
      mockContext({
        status: "waking",
        isReady: false,
        isWaking: true,
        elapsedSeconds: 32,
      });

      render(<BackendStatusBanner />);

      expect(
        screen.getByText("Starting Search Sphere backend. This may take about a minute.")
      ).toBeDefined();
      expect(screen.getByText(/32s elapsed/i)).toBeDefined();
    });

    it("renders unavailable banner with interactive Retry button during offline", () => {
      const mockState = mockContext({
        status: "offline",
        isReady: false,
        isOffline: true,
      });

      render(<BackendStatusBanner />);

      expect(
        screen.getByText("Backend is currently unavailable. Retry connection.")
      ).toBeDefined();

      const retryBtn = screen.getByRole("button", { name: /retry connection/i });
      fireEvent.click(retryBtn);
      expect(mockState.retry).toHaveBeenCalledTimes(1);
    });

    it("returns null when status is ready and not in transient toast state", () => {
      mockContext({
        status: "ready",
        isReady: true,
      });

      const { container } = render(<BackendStatusBanner />);
      expect(container.firstChild).toBeNull();
    });
  });
});
