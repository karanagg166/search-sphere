"use client";

import React, {
  createContext,
  useContext,
  useEffect,
  useState,
  useRef,
  useCallback,
  useMemo,
  type ReactNode,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import { fetchHealth, fetchReady } from "@/lib/api";

export type BackendStatus = "checking" | "waking" | "ready" | "degraded" | "offline";

export interface BackendAvailabilityContextType {
  status: BackendStatus;
  isReady: boolean;
  isWaking: boolean;
  isChecking: boolean;
  isDegraded: boolean;
  isOffline: boolean;
  elapsedSeconds: number;
  retryCount: number;
  lastChecked: number | null;
  errorMessage: string | null;
  checkAvailability: (force?: boolean) => Promise<boolean>;
  retry: () => Promise<boolean>;
}

const BackendAvailabilityContext = createContext<
  BackendAvailabilityContextType | undefined
>(undefined);

// Configuration Constants for Render Cold-Start Recovery
export const AVAILABILITY_CONFIG = {
  INITIAL_FAST_WAIT_MS: 2000,
  PROBE_TIMEOUT_MS: 15000,
  TOTAL_STARTUP_DEADLINE_MS: 120000,
  BASE_RETRY_DELAY_MS: 2500,
  MAX_RETRY_DELAY_MS: 8000,
  BACKOFF_FACTOR: 1.35,
  STALE_THRESHOLD_MS: 45000,
};

function calculateBackoffDelay(attempt: number): number {
  const base = Math.min(
    AVAILABILITY_CONFIG.MAX_RETRY_DELAY_MS,
    AVAILABILITY_CONFIG.BASE_RETRY_DELAY_MS *
      Math.pow(AVAILABILITY_CONFIG.BACKOFF_FACTOR, attempt)
  );
  // Add ±20% jitter to prevent thundering herd
  const jitter = (Math.random() * 0.4 - 0.2) * base;
  return Math.round(base + jitter);
}

export function BackendAvailabilityProvider({
  children,
}: {
  children: ReactNode;
}) {
  const queryClient = useQueryClient();

  const [status, setStatusState] = useState<BackendStatus>("checking");
  const [elapsedSeconds, setElapsedSeconds] = useState<number>(0);
  const [retryCount, setRetryCountState] = useState<number>(0);
  const [lastChecked, setLastChecked] = useState<number | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // Synchronous refs to prevent race conditions during timer callbacks
  const statusRef = useRef<BackendStatus>("checking");
  const retryCountRef = useRef<number>(0);

  const setStatus = useCallback((next: BackendStatus) => {
    statusRef.current = next;
    setStatusState(next);
  }, []);

  const setRetryCount = useCallback((next: number) => {
    retryCountRef.current = next;
    setRetryCountState(next);
  }, []);

  const activeCheckPromiseRef = useRef<Promise<boolean> | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const retryTimerRef = useRef<NodeJS.Timeout | null>(null);
  const elapsedIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const wakeStartTimeRef = useRef<number | null>(null);
  const isMountedRef = useRef<boolean>(true);
  const triggerCheckRef = useRef<(force?: boolean) => Promise<boolean>>(() =>
    Promise.resolve(false)
  );

  // Clear running timers safely
  const clearTimers = useCallback(() => {
    if (retryTimerRef.current) {
      clearTimeout(retryTimerRef.current);
      retryTimerRef.current = null;
    }
    if (elapsedIntervalRef.current) {
      clearInterval(elapsedIntervalRef.current);
      elapsedIntervalRef.current = null;
    }
  }, []);

  // Stop elapsed timer and reset
  const stopElapsedTracking = useCallback(() => {
    if (elapsedIntervalRef.current) {
      clearInterval(elapsedIntervalRef.current);
      elapsedIntervalRef.current = null;
    }
    wakeStartTimeRef.current = null;
  }, []);

  // Start elapsed timer for waking state
  const startElapsedTracking = useCallback(() => {
    if (!wakeStartTimeRef.current) {
      wakeStartTimeRef.current = Date.now();
    }
    if (!elapsedIntervalRef.current) {
      elapsedIntervalRef.current = setInterval(() => {
        if (!isMountedRef.current || !wakeStartTimeRef.current) return;
        const elapsed = Math.floor((Date.now() - wakeStartTimeRef.current) / 1000);
        setElapsedSeconds(elapsed);
      }, 1000);
    }
  }, []);

  // On successful ready transition, invalidate dependent queries
  const onBecameReady = useCallback(() => {
    stopElapsedTracking();
    clearTimers();
    setElapsedSeconds(0);
    setRetryCount(0);
    setErrorMessage(null);
    setLastChecked(Date.now());
    setStatus("ready");

    // Invalidate queries so components that mounted during cold start automatically update
    queryClient.invalidateQueries({ queryKey: ["documents"] });
    queryClient.invalidateQueries({ queryKey: ["conversations"] });
    queryClient.invalidateQueries({ queryKey: ["health"] });
  }, [queryClient, clearTimers, stopElapsedTracking, setStatus, setRetryCount]);

  // Execute a single check flight with cancellation and deduplication
  const executeCheck = useCallback(
    async (isManualRetry = false): Promise<boolean> => {
      if (!wakeStartTimeRef.current) {
        wakeStartTimeRef.current = Date.now();
      }

      // Offline browser detection
      if (typeof navigator !== "undefined" && !navigator.onLine) {
        stopElapsedTracking();
        clearTimers();
        setStatus("offline");
        setErrorMessage("Internet connection offline. Please check your network.");
        return false;
      }

      // Cancel previous inflight probe if active
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
      const controller = new AbortController();
      abortControllerRef.current = controller;

      // Timeout for this specific probe attempt
      const probeTimeout = setTimeout(() => {
        controller.abort();
      }, AVAILABILITY_CONFIG.PROBE_TIMEOUT_MS);

      // Fast waking transition timer: if request is taking longer than 2s, immediately show "waking"
      let fastWakingTimer: NodeJS.Timeout | null = null;
      if (statusRef.current === "checking" || isManualRetry) {
        fastWakingTimer = setTimeout(() => {
          if (isMountedRef.current && statusRef.current !== "ready") {
            setStatus("waking");
            startElapsedTracking();
          }
        }, AVAILABILITY_CONFIG.INITIAL_FAST_WAIT_MS);
      }

      try {
        // Step 1: Process liveness check (/health)
        await fetchHealth(controller.signal);

        // Process is alive! Now check infrastructure readiness (/ready)
        let isReady = false;
        try {
          const readyData = await fetchReady(controller.signal);
          if (readyData.status === "ready" || readyData.status === "ok") {
            isReady = true;
          }
        } catch (readyErr: any) {
          if (readyErr?.response?.status === 503) {
            // Backend process is up, but database is not ready yet
            if (isMountedRef.current) {
              setStatus("degraded");
              setErrorMessage("Database connectivity unavailable.");
              setLastChecked(Date.now());
              stopElapsedTracking();
            }
            return false;
          }
          // Non-503 or transient error during readiness check
          throw readyErr;
        }

        if (isReady && isMountedRef.current) {
          onBecameReady();
          return true;
        }

        return false;
      } catch (err: any) {
        if (!isMountedRef.current) return false;

        // If user cancelled, don't treat as error
        if (err.name === "CanceledError" || err.name === "AbortError") {
          // Handled
        }

        // Check if total startup deadline exceeded
        const currentElapsed = wakeStartTimeRef.current
          ? Date.now() - wakeStartTimeRef.current
          : 0;

        if (currentElapsed >= AVAILABILITY_CONFIG.TOTAL_STARTUP_DEADLINE_MS) {
          // Total deadline exceeded: stop automated polling and mark offline with Retry
          stopElapsedTracking();
          clearTimers();
          setStatus("offline");
          setErrorMessage("Backend is currently unavailable. Retry connection.");
          return false;
        }

        // Backend is still sleeping or starting up on Render
        setStatus("waking");
        startElapsedTracking();
        const nextAttempt = retryCountRef.current + 1;
        setRetryCount(nextAttempt);

        // Schedule next retry with exponential backoff and jitter if tab is visible
        if (typeof document === "undefined" || document.visibilityState === "visible") {
          const delay = calculateBackoffDelay(nextAttempt);
          if (retryTimerRef.current) clearTimeout(retryTimerRef.current);
          retryTimerRef.current = setTimeout(() => {
            if (isMountedRef.current && statusRef.current === "waking") {
              triggerCheckRef.current(false);
            }
          }, delay);
        }

        return false;
      } finally {
        clearTimeout(probeTimeout);
        if (fastWakingTimer) clearTimeout(fastWakingTimer);
      }
    },
    [
      onBecameReady,
      clearTimers,
      startElapsedTracking,
      stopElapsedTracking,
      setStatus,
      setRetryCount,
    ]
  );

  // Public check method with deduplication
  const checkAvailability = useCallback(
    (force = false): Promise<boolean> => {
      // If forced (e.g. manual user retry), cancel previous inflight request
      if (force) {
        if (abortControllerRef.current) {
          abortControllerRef.current.abort();
          abortControllerRef.current = null;
        }
        activeCheckPromiseRef.current = null;
      } else if (activeCheckPromiseRef.current) {
        // Deduplicate: if a non-forced check is already inflight, return the same promise
        return activeCheckPromiseRef.current;
      }

      // If already ready and not forced, check freshness
      if (statusRef.current === "ready" && !force) {
        return Promise.resolve(true);
      }

      const promise = executeCheck(force).finally(() => {
        activeCheckPromiseRef.current = null;
      });

      activeCheckPromiseRef.current = promise;
      return promise;
    },
    [executeCheck]
  );

  useEffect(() => {
    triggerCheckRef.current = checkAvailability;
  }, [checkAvailability]);

  // Manual retry initiated by the user
  const retry = useCallback(async (): Promise<boolean> => {
    clearTimers();
    wakeStartTimeRef.current = Date.now();
    setElapsedSeconds(0);
    setRetryCount(0);
    setErrorMessage(null);
    setStatus("checking");
    return checkAvailability(true);
  }, [clearTimers, checkAvailability, setStatus, setRetryCount]);

  // Initial startup probe on application mount
  useEffect(() => {
    isMountedRef.current = true;
    checkAvailability(false);

    return () => {
      isMountedRef.current = false;
      clearTimers();
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
    };
  }, [checkAvailability, clearTimers]);

  // Listen to browser network online event
  useEffect(() => {
    const handleOnline = () => {
      if (statusRef.current === "offline" || statusRef.current === "degraded") {
        retry();
      }
    };

    const handleOffline = () => {
      setStatus("offline");
      setErrorMessage("Internet connection offline. Please check your network.");
    };

    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);
    return () => {
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
    };
  }, [retry, setStatus]);

  // Listen to window focus and tab visibility change
  useEffect(() => {
    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        if (statusRef.current === "waking" || statusRef.current === "checking") {
          // Immediately probe without waiting for background interval
          checkAvailability(false);
        } else if (statusRef.current === "ready") {
          // Stale check on return to tab
          if (
            lastChecked &&
            Date.now() - lastChecked > AVAILABILITY_CONFIG.STALE_THRESHOLD_MS
          ) {
            checkAvailability(true);
          }
        }
      } else {
        // Tab hidden: pause active timer to avoid aggressive background polling
        if (retryTimerRef.current) {
          clearTimeout(retryTimerRef.current);
          retryTimerRef.current = null;
        }
      }
    };

    const handleFocus = () => {
      if (statusRef.current === "offline") {
        // Quick check on focus
        checkAvailability(true);
      }
    };

    document.addEventListener("visibilitychange", handleVisibilityChange);
    window.addEventListener("focus", handleFocus);
    return () => {
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      window.removeEventListener("focus", handleFocus);
    };
  }, [checkAvailability, lastChecked]);

  const value = useMemo<BackendAvailabilityContextType>(
    () => ({
      status,
      isReady: status === "ready",
      isWaking: status === "waking",
      isChecking: status === "checking",
      isDegraded: status === "degraded",
      isOffline: status === "offline",
      elapsedSeconds,
      retryCount,
      lastChecked,
      errorMessage,
      checkAvailability,
      retry,
    }),
    [
      status,
      elapsedSeconds,
      retryCount,
      lastChecked,
      errorMessage,
      checkAvailability,
      retry,
    ]
  );

  return (
    <BackendAvailabilityContext.Provider value={value}>
      {children}
    </BackendAvailabilityContext.Provider>
  );
}

export function useBackendAvailability(): BackendAvailabilityContextType {
  const context = useContext(BackendAvailabilityContext);
  if (!context) {
    throw new Error(
      "useBackendAvailability must be used within a BackendAvailabilityProvider"
    );
  }
  return context;
}
