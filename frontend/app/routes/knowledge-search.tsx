import { useState, type FormEvent } from "react";

import { Link } from "react-router";

import { apiRequest } from "../api/session";

interface Result {
  entity: { id: string; type: string; canonical_name: string };
  score: number;
  matched_assertion_ids: string[];
  evidence_ids: string[];
}

export default function KnowledgeSearch() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Result[]>([]);
  const [error, setError] = useState("");
  async function search(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      setResults(
        await apiRequest<Result[]>("/knowledge/search", {
          method: "POST",
          body: JSON.stringify({ query, limit: 20 }),
        }),
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Search failed");
    }
  }
  return (
    <>
      <Link to="/knowledge">← Knowledge profile</Link>
      <h1>Knowledge search</h1>
      <form className="filters" onSubmit={search}>
        <label>
          Search
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            required
          />
        </label>
        <button>Search</button>
      </form>
      {error && <p role="alert">{error}</p>}
      <div className="records">
        {results.map((result) => (
          <article className="record" key={result.entity.id}>
            <p className="eyebrow">
              {result.entity.type} · score {result.score.toFixed(2)}
            </p>
            <h2>
              <Link to={`/knowledge/entities/${result.entity.id}`}>
                {result.entity.canonical_name}
              </Link>
            </h2>
            <p>{result.evidence_ids.length} evidence item(s)</p>
          </article>
        ))}
      </div>
    </>
  );
}
