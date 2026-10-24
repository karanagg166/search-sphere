"use client";

import React from "react";
import { useBackendAvailability } from "@/context/backend-availability-context";
import {
  Activity,
  CheckCircle2,
  Loader2,
  XCircle,
  AlertTriangle,
  RotateCw,
} from "lucide-react";

export function BackendStatusIndicator() {
  const {
    status,
    elapsedSeconds,
    retry,
    isReady,
    isWaking,
    isChecking,
    isDegraded,
    isOffline,
  } = useBackendAvailability();

  return (
    <div
      role="status"
      aria-live="polite"
      className="flex items-center gap-2 px-3 py-1.5 rounded-full border border-border bg-card text-xs font-medium shadow-sm transition-all"
    >
      <Activity
        className={`w-3.5 h-3.5 ${
          isReady
            ? "text-primary"
            : isWaking || isDegraded
            ? "text-amber-400"
            : isOffline
            ? "text-destructive"
            : "text-muted-foreground"
        }`}
      />
      <span>API:</span>

      {isChecking && (
        <span className="text-muted-foreground flex items-center gap-1">
          <Loader2 className="w-3 h-3 animate-spin" />
          <span>Connecting...</span>
        </span>
      )}

      {isWaking && (
        <span
          className="text-amber-400 flex items-center gap-1"
          title="Starting Search Sphere backend. This may take about a minute."
        >
          <Loader2 className="w-3 h-3 animate-spin" />
          <span>Waking up{elapsedSeconds > 0 ? ` (${elapsedSeconds}s)` : "..."}</span>
        </span>
      )}

      {isReady && (
        <span className="text-emerald-400 flex items-center gap-1" title="Backend connected.">
          <CheckCircle2 className="w-3.5 h-3.5" />
          <span>Connected</span>
        </span>
      )}

      {isDegraded && (
        <span className="text-amber-400 flex items-center gap-1.5">
          <AlertTriangle className="w-3.5 h-3.5" />
          <span>Degraded</span>
          <button
            type="button"
            onClick={() => retry()}
            className="text-[11px] underline hover:text-amber-300 ml-0.5 inline-flex items-center gap-0.5"
            title="Retry connection"
          >
            <RotateCw className="w-2.5 h-2.5" /> Retry
          </button>
        </span>
      )}

      {isOffline && (
        <span className="text-red-400 flex items-center gap-1.5">
          <XCircle className="w-3.5 h-3.5" />
          <span>Unavailable</span>
          <button
            type="button"
            onClick={() => retry()}
            className="text-[11px] text-red-300 underline hover:text-red-100 ml-0.5 inline-flex items-center gap-0.5 font-semibold"
            title="Retry connection"
          >
            <RotateCw className="w-2.5 h-2.5" /> Retry
          </button>
        </span>
      )}
    </div>
  );
}
