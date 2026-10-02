import { AnswerSource } from "./search";

export interface StreamMetadata {
  query: string;
  retrieval_query: string;
  rewritten: boolean;
}

export interface StreamDonePayload {
  message_id?: string;
  answer: string;
  sources: AnswerSource[];
}

export interface StreamEventHandlers {
  onMetadata?: (meta: StreamMetadata) => void;
  onSources?: (sources: AnswerSource[]) => void;
  onToken?: (token: string) => void;
  onDone?: (payload: StreamDonePayload) => void;
  onError?: (error: string) => void;
}

/**
 * Stream grounded RAG answers from FastAPI SSE endpoints.
 * Handles reading chunks from the ReadableStream, parsing SSE event/data lines,
 * and dispatching to callbacks without risking dangerous innerHTML execution.
 */
export async function streamAnswer(
  url: string,
  payload: Record<string, any>,
  token: string | null,
  handlers: StreamEventHandlers,
  signal?: AbortSignal
): Promise<void> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
  };
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const response = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
    signal,
  });

  if (!response.ok) {
    const errorText = await response.text();
    let detail = errorText;
    try {
      const parsed = JSON.parse(errorText);
      detail = parsed.detail || detail;
    } catch {
      // keep raw error text
    }
    handlers.onError?.(detail || `HTTP Error ${response.status}`);
    return;
  }

  if (!response.body) {
    handlers.onError?.("No response body received from streaming endpoint.");
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      let currentEvent = "";
      for (const line of lines) {
        const trimmed = line.trim();
        if (trimmed.startsWith("event:")) {
          currentEvent = trimmed.replace("event:", "").trim();
        } else if (trimmed.startsWith("data:")) {
          const dataStr = trimmed.replace("data:", "").trim();
          try {
            const parsed = JSON.parse(dataStr);
            if (currentEvent === "metadata") {
              handlers.onMetadata?.(parsed);
            } else if (currentEvent === "source") {
              handlers.onSources?.(parsed.sources || []);
            } else if (currentEvent === "token") {
              handlers.onToken?.(parsed.token || "");
            } else if (currentEvent === "done") {
              handlers.onDone?.(parsed);
            } else if (currentEvent === "error") {
              handlers.onError?.(parsed.error || "Streaming error occurred.");
            }
          } catch {
            // malformed data chunk
          }
        }
      }
    }
  } catch (err: any) {
    if (err.name === "AbortError") {
      // client cancelled
      return;
    }
    handlers.onError?.(err.message || "Streaming connection failed.");
  } finally {
    reader.releaseLock();
  }
}
