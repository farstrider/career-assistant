import { describe, expect, it } from "vitest";

import { operationProgress } from "./knowledge-imports";

describe("CV import progress", () => {
  it("reports backend progress and clamps invalid values", () => {
    expect(
      operationProgress({ state: "running", progress: { percent: 50 } }),
    ).toBe(50);
    expect(
      operationProgress({ state: "running", progress: { percent: 120 } }),
    ).toBe(100);
    expect(
      operationProgress({ state: "running", progress: { percent: -1 } }),
    ).toBe(0);
  });

  it("finishes successful operations at 100%", () => {
    expect(operationProgress({ state: "succeeded", progress: {} })).toBe(100);
  });
});
