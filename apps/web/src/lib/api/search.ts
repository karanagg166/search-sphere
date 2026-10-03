import { apiClient } from "../api";

export interface ConversationMessage {
  role: "user" | "assistant" | "system";
  content: string;
}

export interface AnswerRequestPayload {
  query: string;
  conversation_context?: ConversationMessage[];
  top_k?: number;
  candidate_k?: number;
  document_id?: string;
  score_threshold?: number;
}

export interface AnswerSource {
  source_id: number;
  document_id: string;
  chunk_index: number;
  start_page: number;
  end_page: number;
  content: string;
  rerank_score: number;
}

export interface AnswerResponse {
  query: string;
  retrieval_query: string;
  rewritten: boolean;
  answer: string;
  sources: AnswerSource[];
}

/**
 * Executes a grounded RAG question-answering request against the FastAPI backend.
 * Uses query rewriting if conversation context is provided, performs hybrid retrieval,
 * reranks candidate chunks, and synthesizes an attributed answer.
 */
export async function answerQuestion(
  payload: AnswerRequestPayload
): Promise<AnswerResponse> {
  const response = await apiClient.post<AnswerResponse>("/answer", payload);
  return response.data;
}
