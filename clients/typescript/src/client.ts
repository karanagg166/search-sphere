import type {
  AnswerResponse,
  CollectionListResponse,
  CollectionResponse,
  DocumentListResponse,
  DocumentRegisterRequest,
  DocumentResponse,
  DocumentUploadResponse,
  SearchResponse,
  SearchSphereConfig,
} from "./types";

export class SearchSphereError extends Error {
  constructor(public statusCode: number, message: string) {
    super(`SearchSphereError (${statusCode}): ${message}`);
    this.name = "SearchSphereError";
  }
}

export class SearchSphereClient {
  private baseUrl: string;
  private apiKey: string;
  private clientId?: string;
  private tenantId: string;
  private timeoutMs: number;

  constructor(config: SearchSphereConfig) {
    this.baseUrl = config.baseUrl.replace(/\/+$/, "");
    this.apiKey = config.apiKey;
    this.clientId = config.clientId;
    this.tenantId = config.tenantId || "default";
    this.timeoutMs = config.timeoutMs || 30000;
  }

  private getHeaders(customTenantId?: string, customSubjectId?: string, customCollectionId?: string): Record<string, string> {
    const headers: Record<string, string> = {
      Authorization: `Bearer ${this.apiKey}`,
      "X-Tenant-ID": customTenantId || this.tenantId,
    };
    if (this.clientId) {
      headers["X-Client-ID"] = this.clientId;
    }
    if (customSubjectId) {
      headers["X-Subject-ID"] = customSubjectId;
    }
    if (customCollectionId) {
      headers["X-Collection-ID"] = customCollectionId;
    }
    return headers;
  }

  private async request<T>(
    endpoint: string,
    options: {
      method?: string;
      body?: unknown;
      headers?: Record<string, string>;
      tenantId?: string;
      subjectId?: string;
      collectionId?: string;
    } = {}
  ): Promise<T> {
    const url = `${this.baseUrl}${endpoint}`;
    const headers: Record<string, string> = {
      ...this.getHeaders(options.tenantId, options.subjectId, options.collectionId),
      ...(options.headers || {}),
    };

    let bodyData: BodyInit | undefined = undefined;
    if (options.body) {
      if (options.body instanceof FormData) {
        bodyData = options.body;
      } else {
        headers["Content-Type"] = "application/json";
        bodyData = JSON.stringify(options.body);
      }
    }

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.timeoutMs);

    try {
      const resp = await fetch(url, {
        method: options.method || "GET",
        headers,
        body: bodyData,
        signal: controller.signal,
      });

      if (!resp.ok) {
        const errText = await resp.text();
        throw new SearchSphereError(resp.status, errText);
      }

      return (await resp.json()) as T;
    } finally {
      clearTimeout(timeout);
    }
  }

  // ==================== Collections ====================

  async createCollection(
    collectionId: string,
    name: string,
    description?: string,
    metadata?: Record<string, unknown>,
    tenantId?: string
  ): Promise<CollectionResponse> {
    return this.request<CollectionResponse>("/api/v1/collections", {
      method: "POST",
      body: { collection_id: collectionId, name, description, metadata },
      tenantId,
    });
  }

  async getCollection(collectionId: string, tenantId?: string): Promise<CollectionResponse> {
    return this.request<CollectionResponse>(`/api/v1/collections/${encodeURIComponent(collectionId)}`, {
      method: "GET",
      tenantId,
    });
  }

  async listCollections(limit = 50, offset = 0, tenantId?: string): Promise<CollectionListResponse> {
    return this.request<CollectionListResponse>(`/api/v1/collections?limit=${limit}&offset=${offset}`, {
      method: "GET",
      tenantId,
    });
  }

  async deleteCollection(collectionId: string, tenantId?: string): Promise<{ success: boolean; message: string }> {
    return this.request<{ success: boolean; message: string }>(`/api/v1/collections/${encodeURIComponent(collectionId)}`, {
      method: "DELETE",
      tenantId,
    });
  }

  // ==================== Documents ====================

  async uploadFile(
    fileBlob: Blob,
    documentId: string,
    fileName = "document.pdf",
    collectionId?: string,
    tenantId?: string
  ): Promise<DocumentUploadResponse> {
    const formData = new FormData();
    formData.append("file", fileBlob, fileName);
    formData.append("document_id", documentId);
    if (collectionId) {
      formData.append("collection_id", collectionId);
    }

    return this.request<DocumentUploadResponse>("/api/v1/documents/upload", {
      method: "POST",
      body: formData,
      tenantId,
    });
  }

  async registerDocument(
    req: DocumentRegisterRequest,
    tenantId?: string
  ): Promise<DocumentResponse> {
    return this.request<DocumentResponse>("/api/v1/documents", {
      method: "POST",
      body: req,
      tenantId,
    });
  }

  async getDocument(documentId: string, tenantId?: string): Promise<DocumentResponse> {
    return this.request<DocumentResponse>(`/api/v1/documents/${encodeURIComponent(documentId)}`, {
      method: "GET",
      tenantId,
    });
  }

  async listDocuments(
    filters?: {
      collectionId?: string;
      ownerSubjectId?: string;
      status?: string;
      limit?: number;
      offset?: number;
    },
    tenantId?: string
  ): Promise<DocumentListResponse> {
    const params = new URLSearchParams();
    if (filters?.collectionId) params.set("collection_id", filters.collectionId);
    if (filters?.ownerSubjectId) params.set("owner_subject_id", filters.ownerSubjectId);
    if (filters?.status) params.set("status", filters.status);
    if (filters?.limit) params.set("limit", String(filters.limit));
    if (filters?.offset) params.set("offset", String(filters.offset));

    const qs = params.toString() ? `?${params.toString()}` : "";
    return this.request<DocumentListResponse>(`/api/v1/documents${qs}`, {
      method: "GET",
      tenantId,
    });
  }

  async deleteDocument(documentId: string, tenantId?: string): Promise<{ success: boolean; message: string }> {
    return this.request<{ success: boolean; message: string }>(`/api/v1/documents/${encodeURIComponent(documentId)}`, {
      method: "DELETE",
      tenantId,
    });
  }

  async getSignedUrl(documentId: string, expiresIn = 600, tenantId?: string): Promise<{ url: string; expires_in: number }> {
    return this.request<{ url: string; expires_in: number }>(
      `/api/v1/documents/${encodeURIComponent(documentId)}/signed-url?expires_in=${expiresIn}`,
      { method: "GET", tenantId }
    );
  }

  // ==================== Search & Answers ====================

  async search(
    query: string,
    options?: {
      collectionId?: string;
      ownerSubjectId?: string;
      limit?: number;
      documentType?: string;
      metadataFilters?: Record<string, unknown>;
    },
    tenantId?: string
  ): Promise<SearchResponse> {
    return this.request<SearchResponse>("/api/v1/search", {
      method: "POST",
      body: {
        query,
        collection_id: options?.collectionId,
        owner_subject_id: options?.ownerSubjectId,
        limit: options?.limit ?? 10,
        document_type: options?.documentType,
        metadata_filters: options?.metadataFilters,
      },
      tenantId,
    });
  }

  async generateAnswer(
    query: string,
    options?: {
      collectionId?: string;
      ownerSubjectId?: string;
      limit?: number;
      systemPrompt?: string;
      conversationHistory?: Array<{ role: string; content: string }>;
      metadataFilters?: Record<string, unknown>;
    },
    tenantId?: string
  ): Promise<AnswerResponse> {
    return this.request<AnswerResponse>("/api/v1/answers", {
      method: "POST",
      body: {
        query,
        collection_id: options?.collectionId,
        owner_subject_id: options?.ownerSubjectId,
        limit: options?.limit ?? 5,
        system_prompt: options?.systemPrompt,
        conversation_history: options?.conversationHistory,
        metadata_filters: options?.metadataFilters,
      },
      tenantId,
    });
  }
}
