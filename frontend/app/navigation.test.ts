import { describe, expect, it } from "vitest";

import { destinationForSession, navigationForRoles } from "./navigation";

describe("role-aware navigation", () => {
  it("does not expose administrator navigation to a member", () => {
    expect(navigationForRoles(["member"]).map(({ label }) => label)).toEqual([
      "Overview",
      "Opportunities",
      "Password",
    ]);
  });

  it("exposes account administration to an administrator", () => {
    expect(
      navigationForRoles(["member", "admin"]).map(({ label }) => label),
    ).toContain("Accounts");
  });
});

describe("session routing", () => {
  it("routes unauthenticated and temporary-password sessions safely", () => {
    expect(destinationForSession(null, "/")).toBe("/login");
    expect(destinationForSession(null, "/login")).toBeNull();
    expect(destinationForSession({ must_change_password: true }, "/")).toBe(
      "/account/password",
    );
    expect(
      destinationForSession(
        { must_change_password: true },
        "/account/password",
      ),
    ).toBeNull();
  });
});
