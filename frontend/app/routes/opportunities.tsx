import { useEffect, useState } from "react";

import { Form, Link, useSearchParams } from "react-router";

import { apiRequest } from "../api/session";

export interface JobSummary {
  id: string;
  title: string;
  company_name: string;
  location: string | null;
  remote_policy: string;
  employment_type: string | null;
  status: string;
  discovered_at: string;
  posting_date: string | null;
  sources: { key: string; url: string }[];
}

interface JobPage {
  items: JobSummary[];
  next_cursor: string | null;
  has_more: boolean;
}

export function jobQuery(search: URLSearchParams) {
  const query = search.toString();
  return query ? `/jobs?${query}` : "/jobs";
}

export default function Opportunities() {
  const [search] = useSearchParams();
  const [page, setPage] = useState<JobPage | null>(null);
  const [error, setError] = useState("");
  const query = jobQuery(search);
  useEffect(() => {
    setError("");
    void apiRequest<JobPage>(query)
      .then(setPage)
      .catch((caught) => {
        setError(
          caught instanceof Error
            ? caught.message
            : "Opportunities could not be loaded",
        );
      });
  }, [query]);

  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">Shared job corpus</p>
          <h1>Opportunities</h1>
        </div>
        <span>{page ? `${page.items.length} shown` : "Loading"}</span>
      </div>
      <Form className="filters" method="get">
        <label>
          Search
          <input name="q" defaultValue={search.get("q") ?? ""} />
        </label>
        <label>
          Source
          <input name="source" defaultValue={search.get("source") ?? ""} />
        </label>
        <label>
          Location
          <input name="location" defaultValue={search.get("location") ?? ""} />
        </label>
        <label>
          Remote policy
          <select
            name="remote_policy"
            defaultValue={search.get("remote_policy") ?? ""}
          >
            <option value="">Any</option>
            <option value="onsite">On-site</option>
            <option value="hybrid">Hybrid</option>
            <option value="remote_country">Remote in country</option>
            <option value="remote_region">Remote in region</option>
            <option value="remote_global">Remote globally</option>
            <option value="unspecified">Not provided</option>
          </select>
        </label>
        <label>
          Sort
          <select name="sort" defaultValue={search.get("sort") ?? "discovered"}>
            <option value="discovered">Recently discovered</option>
            <option value="posting">Posting date</option>
            <option value="company">Company</option>
          </select>
        </label>
        <button>Apply filters</button>
      </Form>
      {error && <p role="alert">{error}</p>}
      {page?.items.length === 0 && <p>No opportunities match these filters.</p>}
      <div className="opportunity-grid">
        {page?.items.map((job) => (
          <article className="opportunity" key={job.id}>
            <p className="eyebrow">
              {job.sources.map(({ key }) => key).join(", ")}
            </p>
            <h2>
              <Link to={`/opportunities/${job.id}`}>{job.title}</Link>
            </h2>
            <p className="company" dir="auto">
              {job.company_name}
            </p>
            <dl>
              <div>
                <dt>Location</dt>
                <dd dir="auto">{job.location ?? "Not provided"}</dd>
              </div>
              <div>
                <dt>Remote</dt>
                <dd>{job.remote_policy.replaceAll("_", " ")}</dd>
              </div>
              <div>
                <dt>Discovered</dt>
                <dd>{new Date(job.discovered_at).toLocaleDateString()}</dd>
              </div>
            </dl>
          </article>
        ))}
      </div>
      {page?.next_cursor && (
        <Link
          className="button-link"
          to={`?${new URLSearchParams({ ...Object.fromEntries(search), cursor: page.next_cursor }).toString()}`}
        >
          Next page
        </Link>
      )}
    </>
  );
}
