import { index, route, type RouteConfig } from "@react-router/dev/routes";

export default [
  index("routes/home.tsx"),
  route("login", "routes/login.tsx"),
  route("account/password", "routes/password.tsx"),
  route("opportunities", "routes/opportunities.tsx"),
  route("opportunities/:jobId", "routes/opportunity.tsx"),
  route("knowledge", "routes/knowledge.tsx"),
  route("knowledge/search", "routes/knowledge-search.tsx"),
  route("knowledge/entities/:entityId", "routes/knowledge-entity.tsx"),
  route("knowledge/graph", "routes/knowledge-graph.tsx"),
  route("knowledge/imports", "routes/knowledge-imports.tsx"),
  route("knowledge/history", "routes/knowledge-history.tsx"),
  route("reviews", "routes/reviews.tsx"),
  route("reviews/:proposalId", "routes/review.tsx"),
  route("admin/users", "routes/admin-users.tsx"),
  route("admin/users/:userId", "routes/admin-user.tsx"),
  route("operations", "routes/operations.tsx"),
  route("operations/sources/:sourceId", "routes/source.tsx"),
] satisfies RouteConfig;
