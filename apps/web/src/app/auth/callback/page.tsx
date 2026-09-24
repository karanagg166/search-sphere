"use client";

import React, { useEffect, useState, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useAuth } from "@/context/auth-context";
import { Loader2, AlertCircle, CheckCircle2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import Link from "next/link";

function CallbackHandler() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { setSession } = useAuth();
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [isSuccess, setIsSuccess] = useState<boolean>(false);

  useEffect(() => {
    const token = searchParams.get("token");
    const error = searchParams.get("error");
    const provider = searchParams.get("provider") || "OAuth";

    if (error) {
      setErrorMsg(`Authentication failed via ${provider}: ${error}`);
      return;
    }

    if (token) {
      setSession(token)
        .then(() => {
          setIsSuccess(true);
          setTimeout(() => {
            router.push("/");
          }, 1000);
        })
        .catch((err) => {
          console.error("Failed to establish session:", err);
          setErrorMsg("Could not verify your authenticated session token.");
        });
    } else {
      setErrorMsg("No authorization token received in callback.");
    }
  }, [searchParams, setSession, router]);

  if (errorMsg) {
    return (
      <div className="min-h-[50vh] flex flex-col items-center justify-center p-6 text-center max-w-md mx-auto">
        <div className="w-12 h-12 rounded-full bg-destructive/10 text-destructive flex items-center justify-center mb-4">
          <AlertCircle className="w-6 h-6" />
        </div>
        <h2 className="text-xl font-bold mb-2">Authentication Failed</h2>
        <p className="text-sm text-muted-foreground mb-6">{errorMsg}</p>
        <Link href="/">
          <Button variant="default">Return to Home</Button>
        </Link>
      </div>
    );
  }

  if (isSuccess) {
    return (
      <div className="min-h-[50vh] flex flex-col items-center justify-center p-6 text-center max-w-md mx-auto">
        <div className="w-12 h-12 rounded-full bg-emerald-500/10 text-emerald-400 flex items-center justify-center mb-4">
          <CheckCircle2 className="w-6 h-6" />
        </div>
        <h2 className="text-xl font-bold mb-2">Signed In Successfully!</h2>
        <p className="text-sm text-muted-foreground">Redirecting to your dashboard...</p>
      </div>
    );
  }

  return (
    <div className="min-h-[50vh] flex flex-col items-center justify-center p-6 text-center max-w-md mx-auto">
      <Loader2 className="w-8 h-8 text-primary animate-spin mb-4" />
      <h2 className="text-lg font-semibold mb-1">Completing Sign In...</h2>
      <p className="text-xs text-muted-foreground">Exchanging authentication tokens and establishing session.</p>
    </div>
  );
}

export default function AuthCallbackPage() {
  return (
    <main className="min-h-screen bg-background text-foreground flex items-center justify-center p-4">
      <Suspense
        fallback={
          <div className="flex flex-col items-center justify-center p-6 text-center">
            <Loader2 className="w-8 h-8 text-primary animate-spin mb-4" />
            <p className="text-xs text-muted-foreground">Loading authentication session...</p>
          </div>
        }
      >
        <CallbackHandler />
      </Suspense>
    </main>
  );
}
