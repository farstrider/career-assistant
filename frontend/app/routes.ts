import { index, route, type RouteConfig } from "@react-router/dev/routes";

export default [
  index("routes/home.tsx"),
  route("login", "routes/login.tsx"),
  route("account/password", "routes/password.tsx"),
  route("admin/users", "routes/admin-users.tsx"),
  route("admin/users/:userId", "routes/admin-user.tsx"),
  route("operations", "routes/operations.tsx"),
] satisfies RouteConfig;
