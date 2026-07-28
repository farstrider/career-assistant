import { useEffect, useState, type FormEvent } from "react";

import { Link, useParams } from "react-router";

import { apiRequest } from "../api/session";
import type { Source, SourceRun } from "./operations";

interface Operation {
  id: string;
  state: string;
  progress: Record<string, unknown>;
  problem_detail: string | null;
}

export default function SourceDetail() {
  const { sourceId } = useParams();
  const [source, setSource] = useState<Source | null>(null);
  const [runs, setRuns] = useState<SourceRun[]>([]);
  const [operation, setOperation] = useState<Operation | null>(null);
  const [message, setMessage] = useState("");

  async function refresh() {
    const [item, history] = await Promise.all([
      apiRequest<Source>(`/sources/${sourceId}`),
      apiRequest<SourceRun[]>(`/sources/${sourceId}/runs`),
    ]);
    setSource(item);
    setRuns(history);
  }
  useEffect(() => void refresh(), [sourceId]);
  useEffect(() => {
    if (!operation || !["queued", "running"].includes(operation.state)) return;
    const timer = window.setTimeout(
      () =>
        void apiRequest<Operation>(`/operations/${operation.id}`).then(
          setOperation,
        ),
      2000,
    );
    return () => window.clearTimeout(timer);
  }, [operation]);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!source) return;
    const data = new FormData(event.currentTarget);
    try {
      const updated = await apiRequest<Source>(`/sources/${source.id}`, {
        method: "PATCH",
        headers: { "If-Match": String(source.version) },
        body: JSON.stringify({
          enabled: data.get("enabled") === "on",
          policy_status: data.get("policy_status"),
          requests_per_minute: Number(data.get("requests_per_minute")),
          policy_notes: data.get("policy_notes"),
          feed_url: data.get("feed_url") || undefined,
          company_name: data.get("company_name") || undefined,
        }),
      });
      setSource(updated);
      setMessage("Source saved.");
    } catch (caught) {
      setMessage(
        caught instanceof Error ? caught.message : "Source was not saved",
      );
    }
  }

  async function run() {
    try {
      const item = await apiRequest<Operation>(`/sources/${sourceId}/runs`, {
        method: "POST",
        headers: { "Idempotency-Key": crypto.randomUUID() },
      });
      setOperation(item);
      setMessage("Source run queued.");
    } catch (caught) {
      setMessage(
        caught instanceof Error ? caught.message : "Source run was not queued",
      );
    }
  }

  if (!source) return <p>Loading source…</p>;
  return (
    <>
      <Link to="/operations">← Operations</Link>
      <div className="page-heading">
        <div>
          <p className="eyebrow">{source.kind}</p>
          <h1>{source.key}</h1>
        </div>
        <button
          type="button"
          onClick={() => void run()}
          disabled={!source.enabled}
        >
          Run now
        </button>
      </div>
      <p aria-live="polite">{operation ? `Run ${operation.state}` : message}</p>
      <form className="panel" onSubmit={save}>
        <label className="inline">
          <input
            name="enabled"
            type="checkbox"
            defaultChecked={source.enabled}
          />{" "}
          Enabled
        </label>
        <label>
          Policy status
          <select name="policy_status" defaultValue={source.policy_status}>
            <option value="pending_review">Pending review</option>
            <option value="approved">Approved</option>
            <option value="rejected">Rejected</option>
          </select>
        </label>
        <p>
          Reviewed:{" "}
          {source.policy_reviewed_at
            ? new Date(source.policy_reviewed_at).toLocaleString()
            : "Not reviewed"}
          {" · "}Terms:{" "}
          {source.terms_reviewed_at
            ? new Date(source.terms_reviewed_at).toLocaleDateString()
            : "Not reviewed"}
          {" · "}Robots:{" "}
          {source.robots_reviewed_at
            ? new Date(source.robots_reviewed_at).toLocaleDateString()
            : "Not applicable"}
          {" · "}Next review:{" "}
          {source.next_review_at
            ? new Date(source.next_review_at).toLocaleString()
            : "Not scheduled"}
        </p>
        <label>
          Requests per minute
          <input
            name="requests_per_minute"
            type="number"
            min="0"
            max="600"
            defaultValue={source.requests_per_minute}
          />
        </label>
        <label>
          Policy notes
          <textarea
            name="policy_notes"
            defaultValue={source.policy_notes ?? ""}
          />
        </label>
        {source.kind === "feed" && (
          <>
            <label>
              Feed URL
              <input
                name="feed_url"
                type="url"
                defaultValue={String(source.safe_config.feed_url ?? "")}
              />
            </label>
            <label>
              Company name
              <input
                name="company_name"
                defaultValue={String(source.safe_config.company_name ?? "")}
              />
            </label>
          </>
        )}
        <button>Save source</button>
      </form>
      <h2>Run history</h2>
      {runs.length === 0 ? (
        <p>This source has not run.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Started</th>
              <th>State</th>
              <th>Fetched</th>
              <th>New</th>
              <th>Changed</th>
              <th>Error</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((item) => (
              <tr key={item.id}>
                <td>{new Date(item.started_at).toLocaleString()}</td>
                <td>{item.status}</td>
                <td>{item.fetched_count}</td>
                <td>{item.new_count}</td>
                <td>{item.changed_count}</td>
                <td>{item.error_code ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
