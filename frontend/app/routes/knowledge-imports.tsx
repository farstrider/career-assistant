import { useEffect, useRef, useState, type FormEvent } from "react";

import { Link } from "react-router";

import { apiRequest } from "../api/session";

interface Artifact {
  id: string;
  filename: string;
  processing_state: string;
  processing_version: number;
  size_bytes: number;
  operation_url: string | null;
}

interface Operation {
  state: string;
  progress: Record<string, unknown>;
  problem_detail: string | null;
}

export function operationProgress(
  operation: Pick<Operation, "state" | "progress">,
) {
  if (operation.state === "succeeded") return 100;
  const percent = operation.progress.percent;
  return typeof percent === "number" ? Math.max(0, Math.min(100, percent)) : 0;
}

export default function KnowledgeImports() {
  const [items, setItems] = useState<Artifact[]>([]);
  const [message, setMessage] = useState("");
  const [progress, setProgress] = useState(0);
  const [operationPath, setOperationPath] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  async function refresh() {
    setItems(await apiRequest<Artifact[]>("/artifacts"));
  }
  useEffect(() => {
    void refresh();
  }, []);
  useEffect(() => {
    if (!operationPath) return;
    const path = operationPath.replace(/^\/api\/v1/, "");
    let cancelled = false;
    async function poll() {
      try {
        const operation = await apiRequest<Operation>(path);
        if (cancelled) return;
        setProgress(operationProgress(operation));
        if (operation.state === "succeeded") {
          setMessage(
            operation.progress.result === "no_proposals"
              ? "No reviewable facts were found. Check that the CV has supported sections."
              : "Finished!",
          );
          setOperationPath(null);
          await refresh();
        } else if (operation.state === "failed") {
          setMessage(operation.problem_detail ?? "Import failed");
          setOperationPath(null);
          await refresh();
        } else {
          window.setTimeout(poll, 2000);
        }
      } catch (caught) {
        if (!cancelled) {
          setMessage(
            caught instanceof Error ? caught.message : "Import failed",
          );
          setOperationPath(null);
        }
      }
    }
    void poll();
    return () => {
      cancelled = true;
    };
  }, [operationPath]);
  async function upload(event: FormEvent) {
    event.preventDefault();
    const file = input.current?.files?.[0];
    if (!file) return;
    const body = new FormData();
    body.append("file", file);
    setProgress(0);
    setMessage("Uploading…");
    try {
      const artifact = await apiRequest<Artifact>("/artifacts", {
        method: "POST",
        headers: { "Idempotency-Key": crypto.randomUUID() },
        body,
      });
      setOperationPath(artifact.operation_url);
      setMessage("Processing…");
      await refresh();
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : "Import failed");
    }
  }
  async function reprocess(item: Artifact) {
    setProgress(0);
    setMessage(`Reprocessing ${item.filename}…`);
    try {
      const artifact = await apiRequest<Artifact>(
        `/artifacts/${item.id}/reprocess`,
        {
          method: "POST",
          headers: { "Idempotency-Key": crypto.randomUUID() },
        },
      );
      setOperationPath(artifact.operation_url);
      await refresh();
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : "Reprocess failed");
    }
  }
  return (
    <>
      <Link to="/knowledge">← Knowledge profile</Link>
      <h1>Artifact imports</h1>
      <p>
        Upload a UTF-8 text CV or extractable-text PDF. Imported facts remain
        proposals until explicitly approved.
      </p>
      <form className="filters" onSubmit={upload}>
        <label>
          CV file
          <input
            ref={input}
            type="file"
            accept=".txt,.text,.pdf,text/plain,application/pdf"
            required
          />
        </label>
        <button>Import CV</button>
      </form>
      <p aria-live="polite">{message}</p>
      {(operationPath || progress === 100) && (
        <div className="progress-status" aria-live="polite">
          <progress max={100} value={progress} />
          <span>{progress}%</span>
        </div>
      )}
      <div className="records">
        {items.map((item) => (
          <article className="record" key={item.id}>
            <h2>{item.filename}</h2>
            <p>
              {item.processing_state} · {item.size_bytes} bytes
            </p>
            {item.operation_url && (
              <Link to={item.operation_url}>View operation</Link>
            )}
            <button type="button" onClick={() => void reprocess(item)}>
              Reprocess
            </button>
          </article>
        ))}
      </div>
    </>
  );
}
