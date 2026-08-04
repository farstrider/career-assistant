import { useEffect, useState } from "react";

import { Link } from "react-router";

import { apiRequest } from "../api/session";

interface Version {
  version: number;
  actor_type: string;
  reason: string;
  created_at: string;
}
interface Change {
  version: number;
  object_type: string;
  object_id: string;
  operation: string;
}

export default function KnowledgeHistory() {
  const [versions, setVersions] = useState<Version[]>([]);
  const [changes, setChanges] = useState<Change[]>([]);
  useEffect(() => {
    void apiRequest<Version[]>("/knowledge/versions").then(setVersions);
  }, []);
  async function show(version: number) {
    setChanges(await apiRequest<Change[]>(`/knowledge/versions/${version}`));
  }
  return (
    <>
      <Link to="/knowledge">← Knowledge profile</Link>
      <h1>Graph history</h1>
      <div className="records">
        {versions.map((item) => (
          <article className="record" key={item.version}>
            <h2>Version {item.version}</h2>
            <p>
              {item.reason} · {new Date(item.created_at).toLocaleString()}
            </p>
            <button onClick={() => void show(item.version)}>
              Show changes
            </button>
          </article>
        ))}
      </div>
      {changes.length > 0 && (
        <section>
          <h2>Selected changes</h2>
          <ul>
            {changes.map((item) => (
              <li key={`${item.version}-${item.object_id}`}>
                {item.operation} {item.object_type} {item.object_id}
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}
