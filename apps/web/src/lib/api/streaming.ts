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

  let reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
  try {
    reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let currentEvent = "message";
    const dataLines: string[] = [];

    const dispatchEvent = () => {
      if (dataLines.length === 0) return;
      const dataStr = dataLines.join("\n").trim();
      dataLines.length = 0;
      const eventType = currentEvent;
      currentEvent = "message";

      if (!dataStr) return;

      try {
        const parsed = JSON.parse(dataStr);
        if (eventType === "metadata") {
          handlers.onMetadata?.(parsed);
        } else if (eventType === "source") {
          handlers.onSources?.(parsed.sources || (Array.isArray(parsed) ? parsed : []));
        } else if (eventType === "token") {
          handlers.onToken?.(parsed.token ?? parsed.text ?? "");
        } else if (eventType === "done") {
          handlers.onDone?.({
            message_id: parsed.message_id,
            answer: parsed.answer ?? "",
            sources: parsed.sources || [],
          });
        } else if (eventType === "error") {
          handlers.onError?.(parsed.error || parsed.detail || "Streaming error occurred.");
        } else {
          // Untyped / message event fallback
          if (parsed.token !== undefined || parsed.text !== undefined) {
            handlers.onToken?.(parsed.token ?? parsed.text ?? "");
          } else if (parsed.answer !== undefined) {
            handlers.onDone?.(parsed);
          } else if (parsed.error !== undefined) {
            handlers.onError?.(parsed.error);
          }
        }
      } catch {
        // Raw non-JSON text fallback
        if (eventType === "token") {
          handlers.onToken?.(dataStr);
        } else if (eventType === "error") {
          handlers.onError?.(dataStr);
        }
      }
    };

    const processBuffer = (flushRemaining: boolean) => {
      const lines = buffer.split(/\r?\n/);
      if (!flushRemaining) {
        buffer = lines.pop() || "";
      } else {
        buffer = "";
      }

      for (const rawLine of lines) {
        const line = rawLine.trimStart();
        if (line.startsWith("event:")) {
          currentEvent = line.slice(6).trim();
        } else if (line.startsWith("data:")) {
          dataLines.push(line.slice(5).trim());
        } else if (line.trim() === "") {
          dispatchEvent();
        }
      }

      if (flushRemaining) {
        dispatchEvent();
      }
    };

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      processBuffer(false);
    }

    // Flush any remaining characters at the end of the stream
    buffer += decoder.decode();
    if (buffer.length > 0) {
      processBuffer(true);
    } else if (dataLines.length > 0) {
      dispatchEvent();
    }
  } catch (err: any) {
    if (err.name === "AbortError") {
      // client cancelled
      return;
    }
    handlers.onError?.(err.message || "Streaming connection failed.");
  } finally {
    if (reader) {
      try {
        reader.releaseLock();
      } catch {
        // ignore lock release error
      }
    }
  }
}
