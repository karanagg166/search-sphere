import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
const hooks = vi.hoisted(() => ({ fail: undefined as undefined | ((error: unknown) => Promise<unknown>), send: vi.fn() }));
vi.mock("axios", () => {
  const client = { interceptors: { request: { use: vi.fn() }, response: { use: (_success: unknown, failure: typeof hooks.fail) => { hooks.fail = failure; } } } };
  return { default: Object.assign(hooks.send, { create: () => client }) };
});
import "./api";
const failure = (method: string, status = 503, data = {}) => ({ config: { method, baseURL: "/api/proxy", headers: { Authorization: "Bearer token-a" } }, response: { status, data } });
beforeEach(() => { hooks.send.mockReset(); localStorage.setItem("search_sphere_token", "token-a"); vi.stubEnv("NEXT_PUBLIC_API_URL", "https://api.example.com"); });
afterEach(() => { localStorage.clear(); vi.unstubAllEnvs(); });

describe("Proxy fallback session and replay boundaries", () => {
  it.each(["post", "put", "patch", "delete"])("never replays %s after an ambiguous upstream failure", async (method) => {
    const error = failure(method);
    await expect(hooks.fail!(error)).rejects.toBe(error);
    expect(hooks.send).not.toHaveBeenCalled();
  });
  it("rejects an old account's retry after a session change", async () => {
    localStorage.setItem("search_sphere_token", "token-b"); const error = failure("get");
    await expect(hooks.fail!(error)).rejects.toBe(error); expect(hooks.send).not.toHaveBeenCalled();
  });
  it("does not reinterpret a provider rejection as a missing proxy", async () => {
    const error = failure("get", 503, { error_code: "provider_not_approved" });
    await expect(hooks.fail!(error)).rejects.toBe(error); expect(hooks.send).not.toHaveBeenCalled();
  });
  it("retains one same-session read fallback", async () => {
    hooks.send.mockResolvedValueOnce({ data: { status: "ok" } }); const error = failure("get");
    await expect(hooks.fail!(error)).resolves.toEqual({ data: { status: "ok" } });
    expect(hooks.send).toHaveBeenCalledTimes(1); expect(hooks.send.mock.calls[0][0].baseURL).toBe("https://api.example.com");
    await expect(hooks.fail!(error)).rejects.toBe(error); expect(hooks.send).toHaveBeenCalledTimes(1);
  });
});
