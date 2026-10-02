import axios from "axios";

export const apiClient = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000",
  headers: {
    "Content-Type": "application/json",
  },
  timeout: 30000,
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

export interface HealthResponse {
  status: string;
  service: string;
  version: string;
  environment: string;
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

export async function fetchHealth(): Promise<HealthResponse> {
  const response = await apiClient.get<HealthResponse>("/health");
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

export async function deleteDocument(documentId: string): Promise<void> {
  await apiClient.delete(`/documents/${documentId}`);
}

export * from "./api/search";

