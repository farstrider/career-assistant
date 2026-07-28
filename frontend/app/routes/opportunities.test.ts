import { describe, expect, it } from "vitest";

import { jobQuery } from "./opportunities";

describe("opportunity filters", () => {
  it("preserves URL-backed filters and cursor state", () => {
    const search = new URLSearchParams({
      q: "platform",
      location: "Tokyo",
      remote_policy: "hybrid",
      cursor: "MjU",
    });

    expect(jobQuery(search)).toBe(
      "/jobs?q=platform&location=Tokyo&remote_policy=hybrid&cursor=MjU",
    );
  });
});
