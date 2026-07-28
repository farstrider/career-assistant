import { useEffect, useState, type FormEvent } from "react";

import { Link, useParams } from "react-router";

import { apiRequest } from "../api/session";
import type { JobSummary } from "./opportunities";

interface JobDetail extends JobSummary {
  canonical_url: string;
  version: number;
  normalized: Record<string, unknown>;
  provenance: Record<string, unknown>;
}

interface Feedback {
  id: string;
  event_type: string;
  occurred_at: string;
  note: string | null;
}

export default function Opportunity() {
  const { jobId } = useParams();
  const [job, setJob] = useState<JobDetail | null>(null);
  const [feedback, setFeedback] = useState<Feedback[]>([]);
  const [message, setMessage] = useState("");

  async function refresh() {
    const [detail, history] = await Promise.all([
      apiRequest<JobDetail>(`/jobs/${jobId}`),
      apiRequest<Feedback[]>(`/jobs/${jobId}/feedback`),
    ]);
    setJob(detail);
    setFeedback(history);
  }
  useEffect(() => void refresh(), [jobId]);

  async function record(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setMessage("Saving feedback…");
    try {
      await apiRequest(`/jobs/${jobId}/feedback`, {
        method: "POST",
        headers: { "Idempotency-Key": crypto.randomUUID() },
        body: JSON.stringify({
          event_type: data.get("event_type"),
          occurred_at: new Date().toISOString(),
          note: data.get("note") || null,
          metadata: {},
        }),
      });
      await refresh();
      form.reset();
      setMessage("Feedback recorded.");
    } catch (caught) {
      setMessage(
        caught instanceof Error ? caught.message : "Feedback was not recorded",
      );
    }
  }

  if (!job) return <p>Loading opportunity…</p>;
  return (
    <>
      <Link to="/opportunities">← Opportunities</Link>
      <div className="page-heading">
        <div>
          <p className="eyebrow">Version {job.version}</p>
          <h1 dir="auto">{job.title}</h1>
          <p className="company" dir="auto">
            {job.company_name}
          </p>
        </div>
        <a
          className="button-link"
          href={job.canonical_url}
          target="_blank"
          rel="noopener noreferrer"
        >
          Open source posting
        </a>
      </div>
      <section className="detail-grid">
        <article className="record">
          <h2>Source facts</h2>
          <dl>
            <div>
              <dt>Location</dt>
              <dd dir="auto">{job.location ?? "Not provided"}</dd>
            </div>
            <div>
              <dt>Remote policy</dt>
              <dd>{job.remote_policy.replaceAll("_", " ")}</dd>
            </div>
            <div>
              <dt>Employment</dt>
              <dd>{job.employment_type ?? "Not provided"}</dd>
            </div>
            <div>
              <dt>Posting date</dt>
              <dd>
                {job.posting_date
                  ? new Date(job.posting_date).toLocaleDateString()
                  : "Not provided"}
              </dd>
            </div>
          </dl>
        </article>
        <article className="record">
          <h2>Description</h2>
          <p className="source-copy" dir="auto">
            {String(job.normalized.description)}
          </p>
        </article>
      </section>
      <section>
        <h2>Feedback</h2>
        <form className="filters" onSubmit={record}>
          <label>
            Outcome
            <select name="event_type" required>
              <option value="interested">Interested</option>
              <option value="ignored">Ignored</option>
              <option value="applied">Applied</option>
              <option value="interview">Interview</option>
              <option value="rejected">Rejected</option>
              <option value="offer">Offer</option>
              <option value="accepted">Accepted</option>
            </select>
          </label>
          <label>
            Note
            <textarea name="note" maxLength={2000} />
          </label>
          <button>Record feedback</button>
        </form>
        <p aria-live="polite">{message}</p>
        {feedback.length === 0 ? (
          <p>No feedback recorded.</p>
        ) : (
          <ol className="timeline">
            {feedback.map((item) => (
              <li key={item.id}>
                <strong>{item.event_type}</strong> ·{" "}
                {new Date(item.occurred_at).toLocaleString()}
                {item.note && <p>{item.note}</p>}
              </li>
            ))}
          </ol>
        )}
      </section>
    </>
  );
}
