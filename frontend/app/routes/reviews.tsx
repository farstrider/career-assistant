import { useEffect, useState } from "react";

import { Link } from "react-router";

import { apiRequest } from "../api/session";

interface Proposal {
  id: string;
  state: string;
  current_graph_version: number;
  assertion: { value: Record<string, unknown>; confidence: number };
  defer_until: string | null;
  evidence: { id: string; locator: string; title: string }[];
}

export default function Reviews() {
  const [items, setItems] = useState<Proposal[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    void apiRequest<Proposal[]>("/knowledge/proposals")
      .then((proposals) =>
        setItems(
          proposals.filter((item) =>
            ["pending", "deferred"].includes(item.state),
          ),
        ),
      )
      .catch((caught) =>
        setError(
          caught instanceof Error
            ? caught.message
            : "Reviews could not be loaded",
        ),
      );
  }, []);
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">Private profile</p>
          <h1>Reviews</h1>
        </div>
        <span>{items.length} awaiting review</span>
      </div>
      {error && <p role="alert">{error}</p>}
      {!error && items.length === 0 && <p>No proposals need your review.</p>}
      <div className="records">
        {items.map((item) => (
          <article className="record" key={item.id}>
            <p className="eyebrow">{item.state}</p>
            <h2 dir="auto">
              {String(
                item.assertion.value.label ??
                  item.assertion.value.skill ??
                  "Knowledge proposal",
              )}
            </h2>
            <p>
              Confidence {Math.round(item.assertion.confidence * 100)}% ·{" "}
              {item.evidence.length} evidence item(s)
            </p>
            {item.defer_until && (
              <p>
                Deferred until {new Date(item.defer_until).toLocaleDateString()}
              </p>
            )}
            <Link className="button-link" to={`/reviews/${item.id}`}>
              Review proposal
            </Link>
          </article>
        ))}
      </div>
    </>
  );
}
