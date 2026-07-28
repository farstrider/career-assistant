export type Role = "member" | "admin";

const items = [
  { href: "/", label: "Overview", roles: ["member"] },
  { href: "/opportunities", label: "Opportunities", roles: ["member"] },
  { href: "/operations", label: "Operations", roles: ["admin"] },
  { href: "/admin/users", label: "Accounts", roles: ["admin"] },
  { href: "/account/password", label: "Password", roles: ["member"] },
] as const;

export function navigationForRoles(roles: Role[]) {
  return items.filter((item) =>
    item.roles.some((role) => roles.includes(role)),
  );
}

export function destinationForSession(
  session: { must_change_password: boolean } | null,
  path: string,
) {
  if (!session) return path === "/login" ? null : "/login";
  if (session.must_change_password && path !== "/account/password")
    return "/account/password";
  if (path === "/login") return "/";
  return null;
}
