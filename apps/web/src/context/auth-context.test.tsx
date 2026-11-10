import React, { useState, useEffect } from "react";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider, useAuth } from "./auth-context";
import { fetchMe, loginUser, signupUser, User, AuthResponse } from "@/lib/api";

const backend = vi.hoisted(() => ({ isReady: false }));
vi.mock("@/context/backend-availability-context", () => ({ useBackendAvailability: () => backend }));
vi.mock("@/lib/api", () => ({ fetchMe: vi.fn(), loginUser: vi.fn(), signupUser: vi.fn() }));
const owner = (id: string): User => ({ id, email: id + "@example.com", name: id, avatar_url: null, auth_provider: "local", is_active: true, created_at: null });
const response = (id: string): AuthResponse => ({ access_token: "token-" + id, token_type: "bearer", expires_in: 300, user: owner(id) });
function deferred<T>() {
  let resolve!: (value: T) => void; let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
let auth: ReturnType<typeof useAuth>;
function Probe() {
  const value = useAuth();
  useEffect(() => { auth = value; }, [value]);
  const [passage, setPassage] = useState("");
  return <div><span>{value.user?.id ?? "anonymous"}</span><span>{passage}</span>
    <button onClick={() => setPassage("PRIVATE OWNER A PASSAGE")}>Show local passage</button></div>;
}
function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const tree = () => <QueryClientProvider client={client}><AuthProvider><Probe /></AuthProvider></QueryClientProvider>;
  const view = render(tree()); return { client, view, tree };
}
beforeEach(() => { localStorage.clear(); vi.clearAllMocks(); backend.isReady = false; });
afterEach(cleanup);

describe("Account session and private browser state", () => {
  it("clears cached private queries and component passages on logout and account switch", async () => {
    const { client } = show();
    await act(() => auth.setSession("token-a", owner("a")));
    client.setQueryData(["documents", "a"], { private: "A" });
    await act(async () => screen.getByRole("button").click());
    expect(screen.getByText("PRIVATE OWNER A PASSAGE")).toBeDefined();
    await act(() => auth.setSession("token-b", owner("b")));
    expect(client.getQueryData(["documents", "a"])).toBeUndefined();
    expect(screen.queryByText("PRIVATE OWNER A PASSAGE")).toBeNull();
    client.setQueryData(["documents", "b"], { private: "B" });
    await act(async () => auth.logout());
    expect(client.getQueryCache().getAll()).toHaveLength(0);
    expect(localStorage.getItem("search_sphere_token")).toBeNull();
    expect(auth.isAuthenticated).toBe(false);
  });
  it.each(["success", "failure"])("ignores a late restore %s from the previous account", async (outcome) => {
    localStorage.setItem("search_sphere_token", "token-a");
    const pending = deferred<User>(); vi.mocked(fetchMe).mockReturnValueOnce(pending.promise);
    show(); await waitFor(() => expect(fetchMe).toHaveBeenCalledTimes(1));
    await act(() => auth.setSession("token-b", owner("b")));
    await act(async () => {
      if (outcome === "success") pending.resolve(owner("a"));
      else pending.reject({ response: { status: 401 } });
      await pending.promise.catch(() => undefined);
    });
    expect(auth.user?.id).toBe("b");
    expect(localStorage.getItem("search_sphere_token")).toBe("token-b");
  });
  it("cannot restore an account after logout", async () => {
    localStorage.setItem("search_sphere_token", "token-a");
    const pending = deferred<User>(); vi.mocked(fetchMe).mockReturnValueOnce(pending.promise);
    show(); await waitFor(() => expect(fetchMe).toHaveBeenCalledTimes(1));
    await act(async () => auth.logout());
    await act(async () => { pending.resolve(owner("a")); await pending.promise; });
    expect(auth.user).toBeNull(); expect(auth.token).toBeNull();
    expect(localStorage.getItem("search_sphere_token")).toBeNull();
  });
  it("preserves a token during a cold outage but waits for verified identity before private access", async () => {
    localStorage.setItem("search_sphere_token", "token-a");
    vi.mocked(fetchMe).mockRejectedValueOnce(new Error("synthetic cold start"));
    const { view, tree } = show();
    await waitFor(() => expect(auth.isLoading).toBe(false));
    expect(auth.token).toBe("token-a"); expect(auth.isAuthenticated).toBe(false);
    vi.mocked(fetchMe).mockResolvedValueOnce(owner("a")); backend.isReady = true;
    view.rerender(tree());
    await waitFor(() => expect(auth.isAuthenticated).toBe(true));
    expect(auth.user?.id).toBe("a");
  });
  it("the newest login wins when requests complete out of order", async () => {
    const first = deferred<AuthResponse>(); const second = deferred<AuthResponse>();
    vi.mocked(loginUser).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    show(); let a!: Promise<void>; let b!: Promise<void>;
    await act(async () => { a = auth.login({ email: "a@example.com", password: "synthetic" }); b = auth.login({ email: "b@example.com", password: "synthetic" }); });
    await act(async () => { second.resolve(response("b")); await b; });
    await act(async () => { first.resolve(response("a")); await a; });
    expect(auth.user?.id).toBe("b"); expect(auth.isLoading).toBe(false);
    expect(localStorage.getItem("search_sphere_token")).toBe("token-b");
  });
  it("does not install a signup response after logout", async () => {
    const pending = deferred<AuthResponse>(); vi.mocked(signupUser).mockReturnValueOnce(pending.promise);
    show(); let signup!: Promise<void>;
    await act(async () => { signup = auth.signup({ email: "a@example.com", password: "synthetic" }); });
    await act(async () => auth.logout());
    await act(async () => { pending.resolve(response("a")); await signup; });
    expect(auth.user).toBeNull(); expect(auth.token).toBeNull(); expect(auth.isLoading).toBe(false);
  });
  it("synchronizes cross-tab identity changes before private data can be shown", async () => {
    const { client, view, tree } = show();
    await act(() => auth.setSession("token-a", owner("a")));
    client.setQueryData(["documents", "a"], { private: "A" });
    localStorage.setItem("search_sphere_active_conv:a", "private-conversation-id");
    await act(async () => {
      localStorage.setItem("search_sphere_token", "token-b");
      window.dispatchEvent(new StorageEvent("storage", { key: "search_sphere_token", storageArea: localStorage, newValue: "token-b" }));
    });
    expect(auth.user).toBeNull(); expect(auth.isAuthenticated).toBe(false);
    expect(client.getQueryData(["documents", "a"])).toBeUndefined();
    expect(localStorage.getItem("search_sphere_active_conv:a")).toBeNull();
    vi.mocked(fetchMe).mockResolvedValueOnce(owner("b")); backend.isReady = true;
    view.rerender(tree());
    await waitFor(() => expect(auth.user?.id).toBe("b"));
  });

});
