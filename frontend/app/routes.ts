import { index, route, type RouteConfig } from "@react-router/dev/routes";

export default [
  index("routes/home.tsx"),
  route("login", "routes/login.tsx"),
  route("account/password", "routes/password.tsx"),
  route("opportunities", "routes/opportunities.tsx"),
  route("opportunities/:jobId", "routes/opportunity.tsx"),
  route("admin/users", "routes/admin-users.tsx"),
  route("admin/users/:userId", "routes/admin-user.tsx"),
  route("operations", "routes/operations.tsx"),
  route("operations/sources/:sourceId", "routes/source.tsx"),
] satisfies RouteConfig;
