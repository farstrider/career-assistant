import { useEffect, useState } from "react";

import { Link, useNavigate, useParams } from "react-router";

import { ApiError, apiRequest } from "../api/session";

interface Proposal {
  id: string;
  state: string;
  base_graph_version: number;
  current_graph_version: number;
  decision_note: string | null;
  defer_until: string | null;
  observation_state: string | null;
  assertion: { value: Record<string, unknown>; confidence: number };
  evidence: {
    id: string;
    title: string;
    excerpt: string | null;
    locator: string;
    source_uri: string;
  }[];
}

export default function Review() {
  const { proposalId } = useParams();
  const navigate = useNavigate();
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [editedValue, setEditedValue] = useState("");
  const [note, setNote] = useState("");
  const [deferUntil, setDeferUntil] = useState("");
  const [message, setMessage] = useState("");

  async function load(overwrite: boolean) {
    if (!proposalId) return;
    const item = await apiRequest<Proposal>(
      `/knowledge/proposals/${proposalId}`,
    );
    setProposal(item);
    if (overwrite) {
      setEditedValue(JSON.stringify(item.assertion.value, null, 2));
      setNote(item.decision_note ?? "");
      setDeferUntil(item.defer_until?.slice(0, 10) ?? "");
    }
  }

  useEffect(() => {
    void load(true).catch((caught) =>
      setMessage(
        caught instanceof Error ? caught.message : "Review could not be loaded",
      ),
    );
  }, [proposalId]);

  async function decide(
    decision: "approve" | "approve_with_edit" | "reject" | "defer",
  ) {
    if (!proposalId || !proposal) return;
    let value: Record<string, unknown> | undefined;
    if (decision === "approve_with_edit") {
      try {
        value = JSON.parse(editedValue) as Record<string, unknown>;
      } catch {
        setMessage("Edited value must be valid JSON.");
        return;
      }
    }
    if (decision === "defer" && !deferUntil) {
      setMessage("Choose a defer date first.");
      return;
    }
    setMessage("");
    try {
      await apiRequest(`/knowledge/proposals/${proposalId}/decision`, {
        method: "POST",
        headers: {
          "If-Match": `"${proposal.current_graph_version}"`,
          "Idempotency-Key": crypto.randomUUID(),
        },
        body: JSON.stringify({
          decision,
          value,
          note: note || undefined,
          defer_until:
            decision === "defer" ? `${deferUntil}T23:59:59Z` : undefined,
        }),
      });
      navigate("/reviews");
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 412) {
        setMessage(
          "This proposal changed elsewhere. Your edits are preserved; review the refreshed evidence.",
        );
        await load(false);
      } else {
        setMessage(
          caught instanceof Error
            ? caught.message
            : "Decision could not be saved",
        );
      }
    }
  }

  if (!proposal) return <p role="status">Loading review…</p>;
  const editable =
    proposal.state === "pending" || proposal.state === "deferred";
  return (
    <>
      <Link to="/reviews">← Reviews</Link>
      <div className="page-heading">
        <div>
          <p className="eyebrow">{proposal.state}</p>
          <h1>Review proposal</h1>
        </div>
        <span>Graph version {proposal.current_graph_version}</span>
      </div>
      {message && <p role="alert">{message}</p>}
      <section className="panel">
        <p>Confidence {Math.round(proposal.assertion.confidence * 100)}%</p>
        <label>
          Proposed value
          <textarea
            rows={8}
            value={editedValue}
            onChange={(event) => setEditedValue(event.target.value)}
            readOnly={!editable}
          />
        </label>
        <label>
          Note
          <textarea
            rows={3}
            value={note}
            onChange={(event) => setNote(event.target.value)}
            readOnly={!editable}
          />
        </label>
        <label>
          Defer until
          <input
            type="date"
            value={deferUntil}
            onChange={(event) => setDeferUntil(event.target.value)}
            disabled={!editable}
          />
        </label>
        {editable && (
          <div className="actions">
            <button onClick={() => void decide("approve")}>Approve</button>
            <button onClick={() => void decide("approve_with_edit")}>
              Approve edited value
            </button>
            <button onClick={() => void decide("reject")}>Reject</button>
            <button onClick={() => void decide("defer")}>Defer</button>
          </div>
        )}
      </section>
      <section>
        <h2>Evidence</h2>
        {proposal.evidence.map((item) => (
          <article className="record" key={item.id}>
            <h3>{item.title}</h3>
            <p>
              {item.locator} · {item.source_uri}
            </p>
            {item.excerpt && <p dir="auto">{item.excerpt}</p>}
          </article>
        ))}
      </section>
    </>
  );
}
