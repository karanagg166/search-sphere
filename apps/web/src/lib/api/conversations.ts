import { apiClient } from "../api";
import { AnswerSource } from "./search";

export interface MessageItem {
  id: string;
  conversation_id: string;
  role: "user" | "assistant";
  content: string;
  original_query?: string | null;
  retrieval_query?: string | null;
  rewritten?: boolean;
  sources?: AnswerSource[] | null;
  created_at: string;
}

export interface ConversationSummary {
  id: string;
  user_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface ConversationDetail {
  id: string;
  user_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  messages: MessageItem[];
}

export interface ConversationAnswerPayload {
  query: string;
  top_k?: number;
  candidate_k?: number;
  document_id?: string;
}

export interface ConversationAnswerResponse {
  conversation_id: string;
  message: MessageItem;
  query: string;
  retrieval_query: string;
  rewritten: boolean;
  answer: string;
  sources: AnswerSource[];
}

export async function createConversation(
  title?: string
): Promise<ConversationSummary> {
  const response = await apiClient.post<ConversationSummary>(
    "/conversations",
    title ? { title } : {}
  );
  return response.data;
}

export async function listConversations(): Promise<ConversationSummary[]> {
  const response = await apiClient.get<ConversationSummary[]>("/conversations");
  return response.data;
}

export async function getConversation(
  conversationId: string
): Promise<ConversationDetail> {
  const response = await apiClient.get<ConversationDetail>(
    `/conversations/${conversationId}`
  );
  return response.data;
}

export async function updateConversationTitle(
  conversationId: string,
  title: string
): Promise<ConversationSummary> {
  const response = await apiClient.put<ConversationSummary>(
    `/conversations/${conversationId}`,
    { title }
  );
  return response.data;
}

export async function deleteConversation(
  conversationId: string
): Promise<void> {
  await apiClient.delete(`/conversations/${conversationId}`);
}

export async function answerInConversation(
  conversationId: string,
  payload: ConversationAnswerPayload
): Promise<ConversationAnswerResponse> {
  const response = await apiClient.post<ConversationAnswerResponse>(
    `/conversations/${conversationId}/answer`,
    payload
  );
  return response.data;
}
