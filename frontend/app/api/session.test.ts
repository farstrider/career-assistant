import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiRequest, loadSession, type Session } from "./session";

afterEach(() => vi.unstubAllGlobals());

describe("session loading", () => {
  it("uses same-origin credentials and keeps the CSRF token in memory", async () => {
    const session: Session = {
      user: {
        id: "019c0000-0000-7000-8000-000000000001",
        username: "member",
        display_name: "Career Member",
      },
      profile_id: "019c0000-0000-7000-8000-000000000002",
      roles: ["member"],
      locale: "en",
      timezone: "Asia/Tokyo",
      must_change_password: false,
      csrf_token: "in-memory-only",
    };
    const fetch = vi.fn().mockResolvedValue(Response.json(session));
    vi.stubGlobal("fetch", fetch);

    await expect(loadSession()).resolves.toEqual(session);
    expect(fetch).toHaveBeenCalledWith("/api/v1/session", {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
  });

  it("does not label multipart artifact uploads as JSON", async () => {
    const fetch = vi.fn().mockResolvedValue(Response.json({ ok: true }));
    vi.stubGlobal("fetch", fetch);
    const body = new FormData();
    body.append("file", new Blob(["cv"], { type: "text/plain" }), "cv.txt");

    await apiRequest("/artifacts", {
      method: "POST",
      headers: { "Idempotency-Key": "upload-1" },
      body,
    });

    const request = fetch.mock.calls[0][1] as RequestInit;
    expect(new Headers(request.headers).has("Content-Type")).toBe(false);
  });

  it("keeps the problem status and code for conflict handling", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ code: "GRAPH_VERSION_MISMATCH", detail: "Reload" }),
          {
            status: 412,
            headers: { "Content-Type": "application/json" },
          },
        ),
      ),
    );
    await expect(
      apiRequest("/knowledge/proposals/1/decision"),
    ).rejects.toMatchObject({
      status: 412,
      code: "GRAPH_VERSION_MISMATCH",
    } satisfies Partial<ApiError>);
  });
});
