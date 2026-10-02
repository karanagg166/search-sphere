import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SearchInput } from "./search-input";
import { LoadingState } from "./loading-state";
import { SourceList } from "./source-list";
import { AnswerCard, ChatTurn } from "./answer-card";
import { AnswerSource } from "@/lib/api/search";

describe("Search Components", () => {
  describe("SearchInput", () => {
    it("renders input field and submit button", () => {
      render(
        <SearchInput
          onSearch={vi.fn()}
          isLoading={false}
          placeholder="Ask a question..."
        />
      );

      const input = screen.getByPlaceholderText("Ask a question...");
      expect(input).toBeDefined();
      const button = screen.getByRole("button", { name: /ask/i });
      expect(button).toBeDefined();
      expect(button.getAttribute("disabled")).not.toBeNull(); // Empty input starts disabled
    });

    it("triggers onSearch with query on form submit", () => {
      const handleSearch = vi.fn();
      render(<SearchInput onSearch={handleSearch} isLoading={false} />);

      const input = screen.getByRole("textbox");
      fireEvent.change(input, { target: { value: "How does caching work?" } });

      const button = screen.getByRole("button", { name: /ask/i });
      expect(button.getAttribute("disabled")).toBeNull();
      fireEvent.click(button);

      expect(handleSearch).toHaveBeenCalledTimes(1);
      expect(handleSearch).toHaveBeenCalledWith("How does caching work?", undefined);
    });

    it("disables input and button during loading", () => {
      const handleSearch = vi.fn();
      render(<SearchInput onSearch={handleSearch} isLoading={true} />);

      const input = screen.getByRole("textbox");
      expect(input.getAttribute("disabled")).not.toBeNull();
      const button = screen.getByRole("button", { name: /ask/i });
      expect(button.getAttribute("disabled")).not.toBeNull();
    });

    it("supports scoping to a specific document", () => {
      const handleSearch = vi.fn();
      const docs = [
        { id: "doc-1", filename: "Handbook.pdf" },
        { id: "doc-2", filename: "Specs.pdf" },
      ];

      render(
        <SearchInput
          onSearch={handleSearch}
          isLoading={false}
          documents={docs}
          selectedDocumentId="doc-1"
          onSelectDocumentId={vi.fn()}
        />
      );

      const input = screen.getByRole("textbox");
      fireEvent.change(input, { target: { value: "What is the policy?" } });
      const button = screen.getByRole("button", { name: /ask/i });
      fireEvent.click(button);

      expect(handleSearch).toHaveBeenCalledWith("What is the policy?", "doc-1");
    });
  });

  describe("LoadingState", () => {
    it("renders searching and generating text indicators", () => {
      const { rerender } = render(<LoadingState stage="searching" />);
      expect(screen.getByText("Searching documents...")).toBeDefined();

      rerender(<LoadingState stage="generating" />);
      expect(screen.getByText("Generating answer...")).toBeDefined();
    });
  });

  describe("SourceList", () => {
    const mockSources: AnswerSource[] = [
      {
        source_id: 1,
        document_id: "doc-abc",
        chunk_index: 2,
        start_page: 10,
        end_page: 12,
        content: "Neural networks utilize backpropagation for weight optimization.",
        rerank_score: 9.15,
      },
      {
        source_id: 2,
        document_id: "doc-xyz",
        chunk_index: 0,
        start_page: 4,
        end_page: 4,
        content: "Gradient descent minimizes the defined loss function.",
        rerank_score: 8.72,
      },
    ];

    it("renders source citations with document names, page numbers, and excerpts", () => {
      const docMap = {
        "doc-abc": "Deep Learning Guide.pdf",
        "doc-xyz": "Math Fundamentals.pdf",
      };

      render(<SourceList sources={mockSources} documentMap={docMap} />);

      expect(screen.getByText("Deep Learning Guide.pdf")).toBeDefined();
      expect(screen.getByText("Pages 10–12")).toBeDefined();
      expect(
        screen.getByText(/Neural networks utilize backpropagation/i)
      ).toBeDefined();

      expect(screen.getByText("Math Fundamentals.pdf")).toBeDefined();
      expect(screen.getByText("Page 4")).toBeDefined();
    });

    it("does not expose internal backend scores like rerank_score", () => {
      render(<SourceList sources={mockSources} />);
      expect(screen.queryByText(/9\.15/)).toBeNull();
      expect(screen.queryByText(/8\.72/)).toBeNull();
      expect(screen.queryByText(/rerank/i)).toBeNull();
      expect(screen.queryByText(/rrf/i)).toBeNull();
    });
  });

  describe("AnswerCard", () => {
    it("renders question, answer, and sources successfully", () => {
      const turn: ChatTurn = {
        id: "turn-1",
        query: "What is backpropagation?",
        retrievalQuery: "backpropagation gradient descent neural networks",
        rewritten: true,
        answer: "Backpropagation computes the gradient of the loss function [1].",
        sources: [
          {
            source_id: 1,
            document_id: "doc-1",
            chunk_index: 0,
            start_page: 1,
            end_page: 2,
            content: "Backpropagation gradient calculation details.",
            rerank_score: 8.5,
          },
        ],
        status: "success",
        timestamp: new Date(),
      };

      render(
        <AnswerCard
          turn={turn}
          documentMap={{ "doc-1": "ML Book.pdf" }}
        />
      );

      expect(screen.getByText("What is backpropagation?")).toBeDefined();
      expect(screen.getByText(/Rewritten for search:/)).toBeDefined();
      expect(
        screen.getByText(/Backpropagation computes the gradient of the loss function/)
      ).toBeDefined();
      expect(screen.getByText("ML Book.pdf")).toBeDefined();
      expect(screen.getByText("Pages 1–2")).toBeDefined();
    });

    it("renders user-friendly error message when status is error", () => {
      const turn: ChatTurn = {
        id: "turn-err",
        query: "Trigger error",
        status: "error",
        errorMessage: "Document service timeout",
        timestamp: new Date(),
      };

      const handleRetry = vi.fn();
      render(<AnswerCard turn={turn} onRetry={handleRetry} />);

      expect(
        screen.getByText("Unable to generate answer. Please try again.")
      ).toBeDefined();
      expect(screen.getByText("Document service timeout")).toBeDefined();

      const retryBtn = screen.getByRole("button", { name: /retry question/i });
      fireEvent.click(retryBtn);
      expect(handleRetry).toHaveBeenCalledWith("Trigger error");
    });

    it("renders feedback buttons and triggers onFeedback on click", () => {
      const turn: ChatTurn = {
        id: "msg-123",
        query: "What is caching?",
        answer: "Caching stores copies of data in fast memory.",
        status: "success",
        timestamp: new Date(),
      };
      const handleFeedback = vi.fn();
      render(<AnswerCard turn={turn} onFeedback={handleFeedback} />);

      expect(screen.getByText("Was this answer accurate?")).toBeDefined();
      const thumbsUp = screen.getByRole("button", { name: /thumbs up/i });
      fireEvent.click(thumbsUp);

      expect(handleFeedback).toHaveBeenCalledWith("msg-123", 1);
      expect(screen.getByText("Feedback saved")).toBeDefined();
    });
  });
});

