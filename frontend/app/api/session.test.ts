import { afterEach, describe, expect, it, vi } from "vitest";

import { loadSession, type Session } from "./session";

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
});
