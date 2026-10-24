"use client";

import React, { useEffect, useState, useRef } from "react";
import { useBackendAvailability } from "@/context/backend-availability-context";
import { Loader2, Zap, CheckCircle2, AlertTriangle, RefreshCw, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";

export function BackendStatusBanner() {
  const { status, elapsedSeconds, retry, isReady, isWaking, isOffline, isDegraded } =
    useBackendAvailability();

  // Show a temporary "Backend connected." toast when transitioning to ready from waking/offline
  const [showConnectedToast, setShowConnectedToast] = useState(false);
  const prevStatusRef = useRef(status);

  useEffect(() => {
    const prev = prevStatusRef.current;
    if ((prev === "waking" || prev === "offline" || prev === "degraded") && status === "ready") {
      setShowConnectedToast(true);
      const timer = setTimeout(() => {
        setShowConnectedToast(false);
      }, 3500);
      return () => clearTimeout(timer);
    }
    prevStatusRef.current = status;
  }, [status]);

  if (showConnectedToast) {
    return (
      <div
        role="status"
        aria-live="polite"
        className="fixed bottom-4 right-4 z-50 max-w-sm rounded-xl border border-emerald-500/30 bg-emerald-950/80 backdrop-blur-md p-4 shadow-lg text-emerald-200 animate-in fade-in slide-in-from-bottom-3 duration-300"
      >
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-lg bg-emerald-500/20 text-emerald-400 shrink-0">
            <CheckCircle2 className="w-4 h-4" />
          </div>
          <div className="flex-1 text-xs">
            <div className="font-semibold text-emerald-300">Backend connected.</div>
            <p className="mt-0.5 text-emerald-200/80 text-[11px]">
              Search Sphere is fully ready.
            </p>
          </div>
        </div>
      </div>
    );
  }

  if (isWaking) {
    return (
      <div
        role="status"
        aria-live="polite"
        className="fixed bottom-4 right-4 z-50 max-w-sm rounded-xl border border-amber-500/30 bg-card/95 backdrop-blur-md p-4 shadow-xl text-amber-200 animate-in fade-in slide-in-from-bottom-3 duration-300"
      >
        <div className="flex items-start gap-3">
          <div className="p-2 rounded-lg bg-amber-500/20 text-amber-400 shrink-0 mt-0.5">
            <Loader2 className="w-4 h-4 animate-spin" />
          </div>
          <div className="flex-1 text-xs">
            <div className="font-semibold flex items-center gap-1.5 text-amber-300">
              <Zap className="w-3.5 h-3.5" />
              Starting Search Sphere backend. This may take about a minute.
            </div>
            <p className="mt-1 text-muted-foreground text-[11px] leading-relaxed">
              Render instances sleep after inactivity. Automatically waking up
              {elapsedSeconds > 0 ? ` (${elapsedSeconds}s elapsed)` : "..."}
            </p>
          </div>
        </div>
      </div>
    );
  }

  if (isDegraded) {
    return (
      <div
        role="alert"
        aria-live="assertive"
        className="fixed bottom-4 right-4 z-50 max-w-sm rounded-xl border border-amber-500/40 bg-card/95 backdrop-blur-md p-4 shadow-xl text-amber-200 animate-in fade-in slide-in-from-bottom-3 duration-300"
      >
        <div className="flex items-start gap-3">
          <div className="p-2 rounded-lg bg-amber-500/20 text-amber-400 shrink-0 mt-0.5">
            <AlertTriangle className="w-4 h-4" />
          </div>
          <div className="flex-1 text-xs">
            <div className="font-semibold text-amber-300">
              Backend storage or database degraded.
            </div>
            <p className="mt-1 text-muted-foreground text-[11px] leading-relaxed">
              Backend is responding, but database connectivity is not ready.
            </p>
            <Button
              variant="outline"
              size="sm"
              onClick={() => retry()}
              className="mt-2.5 h-7 px-2.5 text-xs border-amber-500/40 hover:bg-amber-500/10 text-amber-200 flex items-center gap-1.5"
            >
              <RefreshCw className="w-3 h-3" />
              Retry connection
            </Button>
          </div>
        </div>
      </div>
    );
  }

  if (isOffline) {
    return (
      <div
        role="alert"
        aria-live="assertive"
        className="fixed bottom-4 right-4 z-50 max-w-sm rounded-xl border border-destructive/40 bg-card/95 backdrop-blur-md p-4 shadow-xl text-destructive-foreground animate-in fade-in slide-in-from-bottom-3 duration-300"
      >
        <div className="flex items-start gap-3">
          <div className="p-2 rounded-lg bg-destructive/20 text-destructive shrink-0 mt-0.5">
            <XCircle className="w-4 h-4" />
          </div>
          <div className="flex-1 text-xs">
            <div className="font-semibold text-foreground">
              Backend is currently unavailable. Retry connection.
            </div>
            <p className="mt-1 text-muted-foreground text-[11px] leading-relaxed">
              Cold start deadline reached or network is unreachable.
            </p>
            <Button
              variant="default"
              size="sm"
              onClick={() => retry()}
              className="mt-2.5 h-7 px-3 text-xs flex items-center gap-1.5 font-medium"
            >
              <RefreshCw className="w-3 h-3" />
              Retry connection
            </Button>
          </div>
        </div>
      </div>
    );
  }

  return null;
}
