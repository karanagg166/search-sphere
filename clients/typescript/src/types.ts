export interface SearchSphereConfig {
  baseUrl: string;
  apiKey: string;
  clientId?: string;
  tenantId?: string;
  timeoutMs?: number;
}

export interface CollectionResponse {
  id: string;
  client_id: string;
  tenant_id: string;
  collection_id: string;
  name: string;
  description?: string | null;
  metadata?: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface CollectionListResponse {
  total: number;
  collections: CollectionResponse[];
}

export interface DocumentUploadResponse {
  storage_key: string;
  mime_type: string;
  file_size: number;
}

export interface DocumentRegisterRequest {
  external_document_id: string;
  storage_key: string;
  file_name: string;
  mime_type: string;
  file_size: number;
  collection_id?: string;
  owner_subject_id?: string;
  document_type?: string;
  metadata?: Record<string, unknown>;
}

export interface DocumentResponse {
  id: string;
  client_id: string;
  tenant_id: string;
  collection_id?: string | null;
  owner_subject_id?: string | null;
  external_document_id: string;
  file_name: string;
  mime_type: string;
  file_size: number;
  document_type: string;
  storage_path: string;
  status: "QUEUED" | "PROCESSING" | "READY" | "FAILED";
  processing_error?: string | null;
  metadata?: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface DocumentListResponse {
  total: number;
  documents: DocumentResponse[];
}

export interface SearchChunkResult {
  chunk_id: string;
  document_id: string;
  text: string;
  score: number;
  rank: number;
  start_page?: number | null;
  end_page?: number | null;
  block_types: string[];
  client_id?: string | null;
  tenant_id?: string | null;
  collection_id?: string | null;
  owner_subject_id?: string | null;
  document_type?: string | null;
  file_name?: string | null;
}

export interface SearchResponse {
  query: string;
  total: number;
  results: SearchChunkResult[];
  duration_ms: number;
}

export interface AnswerCitation {
  citation_number: number;
  chunk_id: string;
  document_id: string;
  file_name?: string | null;
  page_number?: number | null;
  text_snippet: string;
  collection_id?: string | null;
  owner_subject_id?: string | null;
}

export interface AnswerResponse {
  answer: string;
  citations: AnswerCitation[];
  retrieved_chunk_count: number;
  duration_ms: number;
}
