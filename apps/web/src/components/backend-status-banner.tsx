"use client";

import React, { useEffect, useState, useCallback, useRef } from "react";
import { fetchHealth } from "@/lib/api";
import { Loader2, Zap, CheckCircle2 } from "lucide-react";

export function BackendStatusBanner() {
  const [status, setStatus] = useState<"idle" | "waking" | "online" | "offline">("idle");
  const [elapsed, setElapsed] = useState<number>(0);
  const timerRef = useRef<NodeJS.Timeout | null>(null);

  const pingBackend = useCallback(async () => {
    const startTime = Date.now();
    let hasResponded = false;

    // If it takes more than 2 seconds, assume the server is waking from cold sleep
    const slowTimer = setTimeout(() => {
      if (!hasResponded) {
        setStatus("waking");
      }
    }, 2000);

    try {
      await fetchHealth();
      hasResponded = true;
      clearTimeout(slowTimer);
      setStatus("online");
    } catch {
      hasResponded = true;
      clearTimeout(slowTimer);
      // If error, could be still spinning up on Render (502/503 during cold boot)
      setStatus("waking");
    }
  }, []);

  // Poll until online if waking
  useEffect(() => {
    let interval: NodeJS.Timeout | null = null;
    if (status === "waking") {
      interval = setInterval(() => {
        setElapsed((prev) => prev + 2);
        fetchHealth()
          .then(() => {
            setStatus("online");
            setElapsed(0);
          })
          .catch(() => {
            // Keep retrying
          });
      }, 2500);
    } else {
      setElapsed(0);
    }

    return () => {
      if (interval) clearInterval(interval);
    };
  }, [status]);

  // Initial check & auto-wake on tab focus
  useEffect(() => {
    pingBackend();

    const handleVisibility = () => {
      if (document.visibilityState === "visible") {
        pingBackend();
      }
    };

    window.addEventListener("visibilitychange", handleVisibility);
    window.addEventListener("focus", pingBackend);

    return () => {
      window.removeEventListener("visibilitychange", handleVisibility);
      window.removeEventListener("focus", pingBackend);
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [pingBackend]);

  if (status !== "waking") {
    return null;
  }

  return (
    <div className="fixed bottom-4 right-4 z-50 max-w-sm rounded-xl border border-amber-500/30 bg-amber-500/10 backdrop-blur-md p-4 shadow-lg text-amber-200 animate-in fade-in slide-in-from-bottom-3 duration-300">
      <div className="flex items-start gap-3">
        <div className="p-2 rounded-lg bg-amber-500/20 text-amber-400 shrink-0 mt-0.5">
          <Loader2 className="w-4 h-4 animate-spin" />
        </div>
        <div className="flex-1 text-xs">
          <div className="font-semibold flex items-center gap-1.5 text-amber-300">
            <Zap className="w-3.5 h-3.5" />
            Waking Up Cloud Backend
          </div>
          <p className="mt-1 text-amber-200/80 leading-relaxed">
            Render free tier services sleep after 15 mins of inactivity. Spinning back up usually takes ~30–45s
            {elapsed > 0 ? ` (${elapsed}s elapsed)` : "..."}.
          </p>
        </div>
      </div>
    </div>
  );
}
