const providerMessages: Record<string, string> = {
  provider_not_approved:
    "AI processing is blocked for this request. The workspace administrator must review data classification and access before these documents can be used.",
  provider_quota: "The AI service's free usage limit has been reached. Try again after it resets.",
  provider_rate_limit: "The AI service is receiving too many requests. Wait a moment and try again.",
  provider_timeout: "The AI service timed out. Please try again shortly.",
  provider_invalid_response: "The AI service could not produce a verified answer. Please try again.",
  provider_unavailable: "The AI service is temporarily unavailable. Please try again shortly.",
};

export interface StreamFailure {
  status?: number;
  code?: string;
}

export function inferenceErrorMessage(code: unknown, fallback: string): string {
  return typeof code === "string" && Object.hasOwn(providerMessages, code) ? providerMessages[code] : fallback;
}

export function canFallbackToJson(failure: StreamFailure | null): boolean {
  // Only an explicitly unsupported streaming route proves generation did not run.
  // A provider error, transport loss or empty stream can follow a completed turn.
  return !failure?.code && (failure?.status === 404 || failure?.status === 405);
}
