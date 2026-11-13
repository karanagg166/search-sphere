import axios from "axios";
import { canRecoverRead, readBackoff } from "./read-recovery";

const getBaseUrl = (): string => {
  if (process.env.NEXT_PUBLIC_API_URL) {
    return process.env.NEXT_PUBLIC_API_URL.replace(/\/+$/, "");
  }
  if (typeof window !== "undefined") {
    // In browser client without explicit direct URL, fallback to same-origin proxy
    return "/api/proxy";
  }
  return (
    process.env.INTERNAL_API_URL ||
    "http://localhost:8000"
  );
};

export const apiClient = axios.create({
  baseURL: getBaseUrl(),
  headers: {
    "Content-Type": "application/json",
  },
  timeout: 45000,
});

// Automatically inject JWT Bearer token on client requests
apiClient.interceptors.request.use((config) => {
  if (typeof window !== "undefined") {
    const token = localStorage.getItem("search_sphere_token");
    if (token && config.headers) {
      config.headers.Authorization = `Bearer ${token}`;
    }
  }
  return config;
});

// Only same-session GET/HEAD proxy failures may fall back once. Never replay generation or mutations.
apiClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;
    const directUrl = process.env.NEXT_PUBLIC_API_URL?.replace(/\/+$/, "");
    const status = error.response?.status;
    const isProxyFailure =
      status === 404 ||
      status === 502 ||
      status === 503 ||
      status === 504 ||
      error.code === "ERR_NETWORK";

    if (
      typeof window !== "undefined" &&
      directUrl &&
      !originalRequest?._retried &&
      originalRequest?.baseURL === "/api/proxy" &&
      isProxyFailure &&
      ["get", "head"].includes(String(originalRequest?.method || "get").toLowerCase()) &&
      !error.response?.data?.error_code &&
      originalRequest.headers?.Authorization === (localStorage.getItem("search_sphere_token") ? `Bearer ${localStorage.getItem("search_sphere_token")}` : undefined)
    ) {
      originalRequest._retried = true;
      originalRequest.baseURL = directUrl;
      return axios(originalRequest);
    }
    if (typeof window !== "undefined" && canRecoverRead(error, localStorage.getItem("search_sphere_token"))) {
      const session = localStorage.getItem("search_sphere_token");
      const attempt = originalRequest._coldRetries || 0;
      originalRequest._coldRetries = attempt + 1;
      window.dispatchEvent(new Event("search-sphere:reconnecting"));
      await new Promise((resolve) => setTimeout(resolve, readBackoff(attempt)));
      if (session !== localStorage.getItem("search_sphere_token") || originalRequest.signal?.aborted) return Promise.reject(error);
      return apiClient(originalRequest);
    }
    return Promise.reject(error);
  }
);

export interface HealthResponse {
  status: string;
  service: string;
  version: string;
  environment: string;
  git_commit?: string;
}

export interface ReadyResponse {
  status: string;
  database: string;
  environment: string;
  git_commit?: string;
  error?: string;
}

export interface User {
  id: string;
  email: string;
  name: string | null;
  avatar_url: string | null;
  auth_provider: "local" | "google" | "github";
  is_active: boolean;
  created_at: string | null;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: User;
}

export interface SignupPayload {
  name?: string;
  email: string;
  password: string;
}

export interface LoginPayload {
  email: string;
  password: string;
}

export async function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  const response = await apiClient.get<HealthResponse>("/health", { signal });
  return response.data;
}

export async function fetchReady(signal?: AbortSignal): Promise<ReadyResponse> {
  const response = await apiClient.get<ReadyResponse>("/ready", { signal });
  return response.data;
}

export async function signupUser(payload: SignupPayload): Promise<AuthResponse> {
  const response = await apiClient.post<AuthResponse>("/auth/signup", payload);
  return response.data;
}

export async function loginUser(payload: LoginPayload): Promise<AuthResponse> {
  const response = await apiClient.post<AuthResponse>("/auth/login", payload);
  return response.data;
}

export async function fetchMe(): Promise<User> {
  const response = await apiClient.get<User>("/auth/me");
  return response.data;
}

export interface DocumentItem {
  id: string;
  user_id: string;
  filename: string;
  storage_key: string;
  file_url: string;
  file_size: number;
  mime_type: string;
  status: string;
  job?: { status: string; stage: string; attempts: number; queue_age_seconds: number; error_code?: string | null } | null;
  created_at: string;
  updated_at: string;
}

export interface DocumentListResponse {
  total: number;
  documents: DocumentItem[];
}

export async function uploadDocument(file: File): Promise<DocumentItem> {
  const formData = new FormData();
  formData.append("file", file);
  const response = await apiClient.post<DocumentItem>("/documents", formData, {
    headers: {
      "Content-Type": "multipart/form-data",
    },
  });
  return response.data;
}

export async function fetchDocuments(): Promise<DocumentListResponse> {
  const response = await apiClient.get<DocumentListResponse>("/documents");
  return response.data;
}

export async function fetchDocumentAccessUrl(documentId: string): Promise<string> {
  const response = await apiClient.get<{ url: string }>(
    `/documents/${encodeURIComponent(documentId)}/access`,
  );
  return response.data.url;
}

export async function deleteDocument(documentId: string): Promise<void> {
  await apiClient.delete(`/documents/${documentId}`);
}

export * from "./api/search";

