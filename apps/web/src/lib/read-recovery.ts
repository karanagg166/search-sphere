// Safe reads only. Never replay uploads, auth writes, deletes, generation or payments.
export function canRecoverRead(error: {
  code?: string; response?: { status?: number; data?: { error_code?: string } };
  config?: { method?: string; url?: string; headers?: { Authorization?: string }; signal?: { aborted: boolean }; _coldRetries?: number; _retried?: boolean };
}, currentToken: string | null): boolean {
  const request = error.config;
  if (!request || request._retried || request.signal?.aborted || (request._coldRetries || 0) >= 2) return false;
  if (!["get", "head"].includes((request.method || "get").toLowerCase())) return false;
  if (["/health", "/ready"].includes(request.url || "")) return false; // Availability manager owns probe retrying.
  if (request.headers?.Authorization !== (currentToken ? `Bearer ${currentToken}` : undefined)) return false;
  if (error.response?.data?.error_code) return false; // Approval/quota failures are not cold starts.
  return [502, 503, 504].includes(error.response?.status || 0) || ["ERR_NETWORK", "ECONNABORTED", "ETIMEDOUT"].includes(error.code || "");
}

export function readBackoff(attempt: number): number {
  return Math.min(8000, 2000 * 2 ** attempt);
}
