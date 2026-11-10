import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PrivateTextSearch } from "./private-text-search";
import { searchPrivateText } from "@/lib/api/private-search";

const auth = vi.hoisted(() => ({ user: { id: "owner-a" } as { id: string } | null, isAuthenticated: true }));
vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams() }));
vi.mock("@/context/auth-context", () => ({ useAuth: () => auth }));
vi.mock("@/components/auth-modal", () => ({ AuthModal: () => null }));
vi.mock("@/lib/api", () => ({ fetchDocuments: vi.fn(async () => ({ total: 1, documents: [{ id: "document-a", filename: "public-synthetic.pdf" }] })) }));
vi.mock("@/lib/api/private-search", () => ({ searchPrivateText: vi.fn() }));

beforeEach(() => {
  vi.clearAllMocks();
  auth.user = { id: "owner-a" }; auth.isAuthenticated = true;
});
afterEach(cleanup);

function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const tree = () => <QueryClientProvider client={client}><PrivateTextSearch /></QueryClientProvider>;
  const view = render(tree());
  return { ...view, tree };
}

async function submit() {
  await screen.findByRole("option", { name: "public-synthetic.pdf" });
  fireEvent.change(screen.getByLabelText("Your PDF"), { target: { value: "document-a" } });
  fireEvent.change(screen.getByLabelText("Words or identifier to find"), { target: { value: "PUBLIC-42" } });
  fireEvent.click(screen.getByRole("button", { name: "Find passages" }));
}

const result = { mode: "private_text_search" as const, generated_answer: false as const, external_provider_used: false as const,
  pages_checked: 2, native_text_present: true, results: [{ citation_number: 1, document_id: "document-a", filename: "public-synthetic.pdf", page_number: 2,
    bbox: [1, 2, 3, 4], text: '<script>PUBLIC-42</script>', source_sha256: "a".repeat(64) }] };

describe("Private native PDF text search", () => {
  it("returns cited literal passages using the explicit private endpoint and renders markup inertly", async () => {
    vi.mocked(searchPrivateText).mockResolvedValue(result);
    const { container } = show(); await submit();
    expect(await screen.findByText('<script>PUBLIC-42</script>')).toBeDefined();
    expect(container.querySelector("script")).toBeNull();
    expect(screen.getByText(/Page 2/)).toBeDefined();
    expect(screen.getByText(/No AI generation/)).toBeDefined();
    expect(searchPrivateText).toHaveBeenCalledWith("document-a", "PUBLIC-42");
  });

  it("keeps scan limitations distinct from a text query with no matches", async () => {
    vi.mocked(searchPrivateText).mockResolvedValue({ ...result, native_text_present: false, results: [] });
    show(); await submit();
    expect(await screen.findByText(/Scanned pages need approved OCR/)).toBeDefined();
    expect(screen.queryByText(/No matching text found/)).toBeNull();
  });

  it("withholds an old account's response when identity changes during search", async () => {
    let resolve!: (value: typeof result) => void;
    vi.mocked(searchPrivateText).mockImplementation(() => new Promise((done) => { resolve = done; }));
    const view = show(); await submit();
    auth.user = { id: "owner-b" }; view.rerender(view.tree());
    resolve(result);
    await screen.findByRole("option", { name: "public-synthetic.pdf" });
    expect(screen.queryByText('<script>PUBLIC-42</script>')).toBeNull();
  });

  it("requires sign-in before loading private passages", () => {
    auth.user = null; auth.isAuthenticated = false;
    show();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeDefined();
    expect(screen.queryByRole("button", { name: "Find passages" })).toBeNull();
    expect(searchPrivateText).not.toHaveBeenCalled();
  });
});
