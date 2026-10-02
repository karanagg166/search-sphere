import { describe, it, expect, vi, beforeEach } from "vitest";
import { answerQuestion } from "./search";
import { apiClient } from "../api";

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    apiClient: {
      post: vi.fn(),
    },
  };
});

describe("search API client", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("successfully returns generated answer and sources", async () => {
    const mockResponse = {
      data: {
        query: "How does Redis eviction work?",
        retrieval_query: "Redis cache eviction algorithms LRU LFU",
        rewritten: true,
        answer: "Redis supports several cache eviction policies including allkeys-lru and volatile-lru [1].",
        sources: [
          {
            source_id: 1,
            document_id: "doc-123",
            chunk_index: 0,
            start_page: 5,
            end_page: 6,
            content: "Redis eviction policies overview...",
            rerank_score: 8.95,
          },
        ],
      },
    };

    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValueOnce(mockResponse);

    const payload = {
      query: "How does Redis eviction work?",
      top_k: 5,
      candidate_k: 20,
      conversation_context: [
        { role: "user" as const, content: "Tell me about Redis" },
        { role: "assistant" as const, content: "Redis is an in-memory data store." },
      ],
    };

    const result = await answerQuestion(payload);

    expect(apiClient.post).toHaveBeenCalledTimes(1);
    expect(apiClient.post).toHaveBeenCalledWith("/answer", payload);
    expect(result.answer).toContain("Redis supports several cache eviction policies");
    expect(result.sources).toHaveLength(1);
    expect(result.sources[0].source_id).toBe(1);
    expect(result.rewritten).toBe(true);
  });

  it("handles backend error (e.g. 500 internal server error)", async () => {
    const error = new Error("Request failed with status code 500");
    (error as any).response = {
      status: 500,
      data: { detail: "Failed to generate answer from document context." },
    };

    (apiClient.post as ReturnType<typeof vi.fn>).mockRejectedValueOnce(error);

    await expect(
      answerQuestion({ query: "Trigger error query" })
    ).rejects.toThrow("Request failed with status code 500");
  });

  it("handles authentication failure (401 Unauthorized)", async () => {
    const error = new Error("Request failed with status code 401");
    (error as any).response = {
      status: 401,
      data: { detail: "Could not validate credentials." },
    };

    (apiClient.post as ReturnType<typeof vi.fn>).mockRejectedValueOnce(error);

    await expect(
      answerQuestion({ query: "Unauthorized query" })
    ).rejects.toThrow("Request failed with status code 401");
  });
});
