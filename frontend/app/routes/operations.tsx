import { useEffect, useState } from "react";

import { Link, useOutletContext } from "react-router";

import { apiRequest } from "../api/session";
import type { Session } from "../api/session";

export interface SourceRun {
  id: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  fetched_count: number;
  new_count: number;
  changed_count: number;
  error_code: string | null;
}

export interface Source {
  id: string;
  key: string;
  kind: string;
  enabled: boolean;
  policy_status: string;
  policy_reviewed_at: string | null;
  terms_reviewed_at: string | null;
  robots_reviewed_at: string | null;
  next_review_at: string | null;
  policy_notes: string | null;
  requests_per_minute: number;
  version: number;
  latest_run: SourceRun | null;
  safe_config: Record<string, unknown>;
}

export default function Operations() {
  const session = useOutletContext<Session>();
  const [sources, setSources] = useState<Source[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    if (session.roles.includes("admin")) {
      void apiRequest<Source[]>("/sources")
        .then(setSources)
        .catch((caught) => {
          setError(
            caught instanceof Error
              ? caught.message
              : "Sources could not be loaded",
          );
        });
    }
  }, [session.roles]);
  if (!session.roles.includes("admin"))
    return (
      <>
        <h1>Not authorized</h1>
        <p>Administrator access is required.</p>
      </>
    );
  return (
    <>
      <h1>Operations</h1>
      <p>
        Shared sources remain disabled until their policy record is current and
        approved.
      </p>
      {error && <p role="alert">{error}</p>}
      {sources.length === 0 && !error ? (
        <p>
          No sources are configured. Apply a reviewed source-policy file with
          the administrator CLI.
        </p>
      ) : (
        <div className="records">
          {sources.map((source) => (
            <article className="record" key={source.id}>
              <h2>
                <Link to={`/operations/sources/${source.id}`}>
                  {source.key}
                </Link>
              </h2>
              <p>
                {source.kind} · {source.enabled ? "Enabled" : "Disabled"} ·
                Policy {source.policy_status}
              </p>
              <p>Latest run: {source.latest_run?.status ?? "Never"}</p>
            </article>
          ))}
        </div>
      )}
    </>
  );
}
