import { apiClient } from "../api";

export interface PrivatePassage {
  citation_number: number;
  document_id: string;
  filename: string;
  page_number: number;
  bbox: number[];
  text: string;
  source_sha256: string;
}

export interface PrivateSearchResult {
  mode: "private_text_search";
  generated_answer: false;
  external_provider_used: false;
  pages_checked: number;
  native_text_present: boolean;
  results: PrivatePassage[];
}

export async function searchPrivateText(documentId: string, query: string): Promise<PrivateSearchResult> {
  const response = await apiClient.post<PrivateSearchResult>("/private-search", {
    document_id: documentId, query, limit: 5,
  }, { timeout: 35000 });
  const result = response.data;
  if (result?.mode !== "private_text_search" || result.generated_answer !== false
    || result.external_provider_used !== false || !Array.isArray(result.results)
    || result.results.length > 10 || result.results.some((source) => source.document_id !== documentId
      || !Number.isInteger(source.page_number) || source.page_number < 1
      || typeof source.text !== "string" || source.text.length > 4000
      || !Array.isArray(source.bbox) || source.bbox.length !== 4 || source.bbox.some((value) => !Number.isFinite(value))
      || !/^[a-f0-9]{64}$/.test(source.source_sha256))) {
    throw new Error("Private text-search response could not be verified.");
  }
  return result;
}

export async function fetchProcessingOptions(): Promise<{ ai_answers_available: boolean }> {
  return (await apiClient.get<{ ai_answers_available: boolean }>("/processing-options")).data;
}
