import { afterEach, describe, expect, it, vi } from "vitest";
import { getJson, postJson } from "@/lib/api";

afterEach(() => vi.unstubAllGlobals());

describe("api client", () => {
  it("loads data", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(JSON.stringify({ status: "ok" }), { status: 200 }),
        ),
    );
    await expect(getJson("/api/health")).resolves.toEqual({ status: "ok" });
  });

  it("adds process csrf token to POST", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ csrf_token: "csrf" }), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ status: "accepted" }), { status: 202 }),
      );
    vi.stubGlobal("fetch", fetchMock);
    await postJson("/api/sync-trigger");
    const options = fetchMock.mock.calls[1][1] as RequestInit;
    expect((options.headers as Record<string, string>)["X-CSRF-Token"]).toBe(
      "csrf",
    );
    expect(options.method).toBe("POST");
  });
});
