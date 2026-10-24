import { describe, it, expect, vi } from "vitest";
import { streamAnswer } from "./streaming";

describe("Streaming Cold-Start & Network Interruption Recovery", () => {
  it("reports an explicit interruption message when the stream is abruptly cut off mid-sentence", async () => {
    let callIndex = 0;
    const encoder = new TextEncoder();
    const mockReader = {
      read: vi.fn().mockImplementation(async () => {
        callIndex++;
        if (callIndex === 1) {
          return {
            done: false,
            value: encoder.encode('event: token\ndata: {"token": "Incomplete thought "}\n\n'),
          };
        }
        throw new Error("Network connection closed abruptly.");
      }),
      releaseLock: vi.fn(),
    };

    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      body: {
        getReader: () => mockReader,
      },
    });

    const receivedTokens: string[] = [];
    let receivedError: string | null = null;
    let receivedDone: any = null;

    await streamAnswer(
      "https://search-sphere-api.onrender.com/conversations/conv-1/answer/stream",
      { query: "Explain quantum computing" },
      "test-token",
      {
        onToken: (tok) => receivedTokens.push(tok),
        onError: (err) => {
          receivedError = err;
        },
        onDone: (payload) => {
          receivedDone = payload;
        },
      }
    );

    // Initial token was received
    expect(receivedTokens).toEqual(["Incomplete thought "]);
    // The error was properly dispatched to the handler without crashing
    expect(receivedError).toBe("Network connection closed abruptly.");
    // Done was NOT dispatched since stream was interrupted
    expect(receivedDone).toBeNull();
  });

  it("handles abort signals cleanly without invoking onError callback", async () => {
    const controller = new AbortController();

    const mockBody = new ReadableStream({
      start(ctrl) {
        ctrl.enqueue(new TextEncoder().encode('event: token\ndata: {"token": "Starting "}\n\n'));
      },
    });

    global.fetch = vi.fn().mockImplementation(async (_url, options) => {
      if (options?.signal?.aborted) {
        const err = new Error("The operation was aborted");
        err.name = "AbortError";
        throw err;
      }
      return {
        ok: true,
        body: mockBody,
      };
    });

    controller.abort();

    let onErrorCalled = false;
    await streamAnswer(
      "https://search-sphere-api.onrender.com/conversations/conv-1/answer/stream",
      { query: "Cancel test" },
      "test-token",
      {
        onError: () => {
          onErrorCalled = true;
        },
      },
      controller.signal
    );

    // Abort is a clean user-driven cancellation and should not trigger onError noise
    expect(onErrorCalled).toBe(false);
  });
});
