import {
  Links,
  Meta,
  NavLink,
  Outlet,
  redirect,
  Scripts,
  ScrollRestoration,
  useLoaderData,
} from "react-router";

import type { Route } from "./+types/root";
import { apiRequest, loadSession } from "./api/session";
import { destinationForSession, navigationForRoles } from "./navigation";
import "./styles/tokens.css";
import "./styles/app.css";

export async function clientLoader({ request }: Route.ClientLoaderArgs) {
  const path = new URL(request.url).pathname;
  try {
    const session = await loadSession();
    const destination = destinationForSession(session, path);
    if (destination) throw redirect(destination);
    return session;
  } catch (error) {
    const status = (error as { status?: number } | null)?.status;
    if (status === 401) {
      const destination = destinationForSession(null, path);
      if (destination) throw redirect(destination);
      return null;
    }
    throw error;
  }
}
clientLoader.hydrate = true as const;

export function HydrateFallback() {
  return <main className="loading">Loading secure session…</main>;
}

export function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <Meta />
        <Links />
      </head>
      <body>
        {children}
        <ScrollRestoration />
        <Scripts />
      </body>
    </html>
  );
}

export default function App(_: Route.ComponentProps) {
  const session = useLoaderData<typeof clientLoader>();
  if (!session) return <Outlet />;
  const navigation = navigationForRoles(session.roles);
  return (
    <div className="shell">
      <header>
        <NavLink className="brand" to="/">
          Career Assistant
        </NavLink>
        <span>{session.user.display_name}</span>
        <button
          type="button"
          onClick={() => {
            void apiRequest("/auth/logout", { method: "POST" }).then(() => {
              window.location.assign("/login");
            });
          }}
        >
          Sign out
        </button>
      </header>
      <nav aria-label="Primary">
        {navigation.map((item) => (
          <NavLink key={item.href} to={item.href}>
            {item.label}
          </NavLink>
        ))}
      </nav>
      <main>
        <Outlet context={session} />
      </main>
    </div>
  );
}

export function ErrorBoundary({ error }: Route.ErrorBoundaryProps) {
  const message =
    error instanceof Error
      ? error.message
      : "The application could not be loaded.";
  return (
    <main>
      <h1>Something went wrong</h1>
      <p>{message}</p>
    </main>
  );
}
