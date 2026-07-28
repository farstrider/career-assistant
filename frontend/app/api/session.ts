import type { components } from "./schema";

export type Session = components["schemas"]["SessionResponse"];
let csrfToken: string | undefined;

export async function loadSession(): Promise<Session> {
  const response = await fetch("/api/v1/session", {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  });
  if (!response.ok)
    throw new Response("Authentication required", { status: response.status });
  const session = (await response.json()) as Session;
  csrfToken = session.csrf_token;
  return session;
}

export async function apiRequest<T>(path: string, init: RequestInit = {}) {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body) headers.set("Content-Type", "application/json");
  if (init.method && init.method !== "GET") {
    if (csrfToken) headers.set("X-CSRF-Token", csrfToken);
  }
  const response = await fetch(`/api/v1${path}`, {
    ...init,
    credentials: "same-origin",
    headers,
  });
  if (response.status === 401) {
    csrfToken = undefined;
    window.location.assign("/login");
  }
  if (!response.ok) {
    const problem = (await response.json().catch(() => null)) as {
      detail?: string;
    } | null;
    throw new Error(problem?.detail ?? "Request failed");
  }
  if (response.status === 204) return undefined as T;
  const value = (await response.json()) as T;
  if (path === "/auth/login" || path === "/auth/password") {
    csrfToken = (value as Session).csrf_token;
  }
  return value;
}
