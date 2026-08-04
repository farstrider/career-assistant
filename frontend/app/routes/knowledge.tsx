import { useEffect, useState } from "react";

import { Link } from "react-router";

import { apiRequest } from "../api/session";

interface Entity {
  id: string;
  type: string;
  canonical_name: string;
  attributes: Record<string, unknown>;
  assertions: { id: string; predicate: string; status: string }[];
  graph_version: number;
}

export default function Knowledge() {
  const [entities, setEntities] = useState<Entity[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    void apiRequest<{ items: Entity[] }>("/knowledge/entities")
      .then((page) => setEntities(page.items))
      .catch((caught) =>
        setError(
          caught instanceof Error
            ? caught.message
            : "Knowledge could not be loaded",
        ),
      );
  }, []);
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">Private profile</p>
          <h1>Knowledge profile</h1>
        </div>
        <div className="actions">
          <Link className="button-link" to="/knowledge/search">
            Search
          </Link>
          <Link className="button-link" to="/knowledge/graph">
            Graph
          </Link>
        </div>
      </div>
      {error && <p role="alert">{error}</p>}
      {!error && entities.length === 0 && (
        <p>
          No confirmed knowledge yet. Import a CV to create reviewable
          proposals.
        </p>
      )}
      <div className="records">
        {entities.map((entity) => (
          <article className="record" key={entity.id}>
            <p className="eyebrow">{entity.type}</p>
            <h2 dir="auto">
              <Link to={`/knowledge/entities/${entity.id}`}>
                {entity.canonical_name}
              </Link>
            </h2>
            <p>{entity.assertions.length} confirmed assertion(s)</p>
          </article>
        ))}
      </div>
      <p>
        <Link to="/knowledge/imports">Import history</Link> ·{" "}
        <Link to="/knowledge/history">Graph history</Link>
      </p>
    </>
  );
}
