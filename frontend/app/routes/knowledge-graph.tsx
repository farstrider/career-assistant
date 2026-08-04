import cytoscape from "cytoscape";
import { useEffect, useRef, useState } from "react";

import { Link } from "react-router";

import { apiRequest } from "../api/session";

interface Entity {
  id: string;
  canonical_name: string;
  type: string;
}
interface Path {
  entities: string[];
  relations: string[];
}

export default function KnowledgeGraph() {
  const [entities, setEntities] = useState<Entity[]>([]);
  const [paths, setPaths] = useState<Path[]>([]);
  const canvas = useRef<HTMLDivElement>(null);
  useEffect(() => {
    void apiRequest<{ items: Entity[] }>("/knowledge/entities").then((page) =>
      setEntities(page.items),
    );
  }, []);
  async function expand(id: string) {
    setPaths(
      await apiRequest<Path[]>("/knowledge/traverse", {
        method: "POST",
        body: JSON.stringify({
          start_entity_ids: [id],
          max_depth: 2,
          max_paths: 50,
          direction: "both",
        }),
      }),
    );
  }
  useEffect(() => {
    if (!canvas.current) return;
    const nodeIds = new Set(paths.flatMap((path) => path.entities));
    const edgeIds = new Set(paths.flatMap((path) => path.relations));
    const elements = [
      ...entities
        .filter((entity) => nodeIds.has(entity.id))
        .map((entity) => ({
          data: { id: entity.id, label: entity.canonical_name },
        })),
      ...paths
        .flatMap((path) =>
          path.relations.map((relation, index) => ({
            data: {
              id: relation,
              source: path.entities[index],
              target: path.entities[index + 1],
              label: relation,
            },
          })),
        )
        .filter((edge) => edgeIds.has(edge.data.id)),
    ];
    const graph = cytoscape({
      container: canvas.current,
      elements,
      layout: { name: "breadthfirst", directed: true },
      style: [
        {
          selector: "node",
          style: {
            label: "data(label)",
            "background-color": "#315c8c",
            color: "#17202a",
            "text-valign": "bottom",
          },
        },
        {
          selector: "edge",
          style: {
            label: "data(label)",
            width: 2,
            "line-color": "#78909c",
            "target-arrow-color": "#78909c",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
          },
        },
      ],
    });
    return () => graph.destroy();
  }, [entities, paths]);
  return (
    <>
      <Link to="/knowledge">← Knowledge profile</Link>
      <h1>Knowledge graph</h1>
      <p>
        The graph is read-only. Select an entity to inspect bounded paths; the
        list below is the accessible keyboard equivalent.
      </p>
      <div className="records">
        {entities.slice(0, 50).map((entity) => (
          <article className="record" key={entity.id}>
            <h2>{entity.canonical_name}</h2>
            <p>{entity.type}</p>
            <button onClick={() => void expand(entity.id)}>Expand paths</button>
          </article>
        ))}
      </div>
      <div
        className="graph-canvas"
        ref={canvas}
        aria-label="Read-only knowledge graph canvas"
        role="img"
      />
      <h2>Path list</h2>
      <ol>
        {paths.map((path, index) => (
          <li key={index}>
            {path.entities.join(" → ")} ({path.relations.join(", ")})
          </li>
        ))}
      </ol>
    </>
  );
}
