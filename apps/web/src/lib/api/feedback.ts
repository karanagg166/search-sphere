import { apiClient } from "../api";

export interface FeedbackPayload {
  rating: 1 | -1;
  comment?: string;
  conversation_id?: string;
  message_id?: string;
}

export interface FeedbackResponse {
  id: string;
  user_id: string;
  conversation_id?: string;
  message_id?: string;
  rating: number;
  comment?: string;
  created_at: string;
}

export async function submitMessageFeedback(
  conversationId: string,
  messageId: string,
  payload: { rating: 1 | -1; comment?: string },
  token?: string
): Promise<FeedbackResponse> {
  const headers = token ? { Authorization: `Bearer ${token}` } : {};
  const response = await apiClient.post<FeedbackResponse>(
    `/conversations/${conversationId}/messages/${messageId}/feedback`,
    payload,
    { headers }
  );
  return response.data;
}

export async function submitGenericFeedback(
  payload: FeedbackPayload,
  token?: string
): Promise<FeedbackResponse> {
  const headers = token ? { Authorization: `Bearer ${token}` } : {};
  const response = await apiClient.post<FeedbackResponse>(
    "/feedback",
    payload,
    { headers }
  );
  return response.data;
}
