import { describe, it, expect, vi } from "vitest";
import { streamAnswer } from "./streaming";

describe("streamAnswer SSE client", () => {
  it("parses events and dispatches tokens and done payload", async () => {
    const sseData = [
      'event: metadata\ndata: {"query": "test query", "retrieval_query": "test query", "rewritten": false}\n\n',
      'event: source\ndata: {"sources": [{"source_id": 1, "document_id": "doc-1", "chunk_index": 0, "start_page": 1, "end_page": 1, "content": "text", "rerank_score": 1.0}]}\n\n',
      'event: token\ndata: {"token": "Hello "}\n\n',
      'event: token\ndata: {"token": "world!"}\n\n',
      'event: done\ndata: {"answer": "Hello world!", "sources": []}\n\n',
    ].join("");

    const mockBody = new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(sseData));
        controller.close();
      },
    });

    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      body: mockBody,
    });
    global.fetch = mockFetch;

    const tokens: string[] = [];
    let receivedMetadata: any = null;
    let receivedDone: any = null;

    await streamAnswer(
      "http://test/answer/stream",
      { query: "test" },
      "dummy-token",
      {
        onMetadata: (m) => {
          receivedMetadata = m;
        },
        onToken: (t) => {
          tokens.push(t);
        },
        onDone: (d) => {
          receivedDone = d;
        },
      }
    );

    expect(receivedMetadata).toEqual({
      query: "test query",
      retrieval_query: "test query",
      rewritten: false,
    });
    expect(tokens.join("")).toBe("Hello world!");
    expect(receivedDone.answer).toBe("Hello world!");
  });

  it("handles HTTP error gracefully", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
      text: async () => JSON.stringify({ detail: "Cohere API unavailable" }),
    });
    global.fetch = mockFetch;

    let receivedError = "";
    await streamAnswer(
      "http://test/answer/stream",
      { query: "test" },
      null,
      {
        onError: (err) => {
          receivedError = err;
        },
      }
    );

    expect(receivedError).toBe("Cohere API unavailable");
  });
});
