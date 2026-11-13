import { describe, expect, it } from "vitest";
import { canRecoverRead, readBackoff } from "./read-recovery";

describe("cold-start replay boundaries", () => {
  const error = (method = "get") => ({ config: { method, url: "/documents", headers: { Authorization: "Bearer a" } }, response: { status: 503 } });
  it.each(["post", "put", "patch", "delete"])("does not replay %s", (method) => {
    expect(canRecoverRead(error(method), "a")).toBe(false);
  });
  it("only retries same-session reads and excludes policy errors", () => {
    expect(canRecoverRead(error(), "a")).toBe(true);
    expect(canRecoverRead(error(), "b")).toBe(false);
    expect(canRecoverRead({ ...error(), response: { status: 503, data: { error_code: "provider_quota" } } }, "a")).toBe(false);
    expect(canRecoverRead({ ...error(), config: { ...error().config, _coldRetries: 2 } }, "a")).toBe(false);
    expect(canRecoverRead({ ...error(), config: { ...error().config, signal: { aborted: true } } }, "a")).toBe(false);
    expect(canRecoverRead({ ...error(), config: { ...error().config, url: "/health" } }, "a")).toBe(false);
    expect(readBackoff(20)).toBe(8000);
  });
});
