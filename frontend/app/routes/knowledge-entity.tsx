import { useEffect, useState } from "react";

import { Link, useParams } from "react-router";

import { apiRequest } from "../api/session";

interface Entity {
  id: string;
  type: string;
  canonical_name: string;
  attributes: Record<string, unknown>;
  assertions: {
    id: string;
    predicate: string;
    value: Record<string, unknown>;
    confidence: number;
    evidence_ids: string[];
  }[];
  graph_version: number;
}
interface Evidence {
  id: string;
  title: string;
  source_uri: string;
  locator: string;
  excerpt: string | null;
}

export default function KnowledgeEntity() {
  const { entityId } = useParams();
  const [entity, setEntity] = useState<Entity | null>(null);
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    void Promise.all([
      apiRequest<Entity>(`/knowledge/entities/${entityId}`),
      apiRequest<Evidence[]>(`/knowledge/entities/${entityId}/evidence`),
    ])
      .then(([loaded, loadedEvidence]) => {
        setEntity(loaded);
        setEvidence(loadedEvidence);
      })
      .catch((caught) =>
        setError(
          caught instanceof Error
            ? caught.message
            : "Entity could not be loaded",
        ),
      );
  }, [entityId]);
  if (error) return <p role="alert">{error}</p>;
  if (!entity) return <p>Loading entity…</p>;
  return (
    <>
      <Link to="/knowledge">← Knowledge profile</Link>
      <p className="eyebrow">
        {entity.type} · graph version {entity.graph_version}
      </p>
      <h1 dir="auto">{entity.canonical_name}</h1>
      <section className="records">
        {entity.assertions.map((assertion) => (
          <article className="record" key={assertion.id}>
            <h2>{assertion.predicate}</h2>
            <p>{JSON.stringify(assertion.value)}</p>
            <p>
              Confidence {assertion.confidence.toFixed(2)} ·{" "}
              {assertion.evidence_ids.length} evidence item(s)
            </p>
          </article>
        ))}
      </section>
      <h2>Evidence</h2>
      <ul>
        {evidence.map((item) => (
          <li key={item.id}>
            <strong>{item.title}</strong> · {item.locator} ·{" "}
            {item.excerpt ?? "Excerpt unavailable"}
          </li>
        ))}
      </ul>
    </>
  );
}
