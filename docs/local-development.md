# Local development

This guide covers developer setup, builds, generated contracts, automated checks, and full-stack smoke testing. Deployment, account recovery, migrations, and routine operations are documented in [System administration](system-administration.md).

## Toolchain

Install:

- Python 3.13
- [uv](https://docs.astral.sh/uv/)
- Node.js 22 and Corepack
- Docker with Compose v2

Install the locked backend and frontend dependencies:

```sh
uv sync --frozen
cd frontend
corepack npm@10.9.4 ci
cd ..
```

The default tests use no external provider, live source, or private career data. Frozen RSS/Atom, sanitized LinkedIn alert, and manual-import fixtures cover normalization, deduplication, schema drift, SSRF, rate limits, MIME safety, IMAP cursor safety, and job-version history. Gmail tests use a fake IMAP client; live mailbox access is always opt-in.

Milestone 4 enrichment is disabled unless `CAREER_LLM_ENDPOINT` and
`CAREER_LLM_MODEL` are configured. The default suite uses frozen provider
fixtures and never sends job content to a live service. For the explicit,
budgeted smoke check, configure an OpenAI-compatible chat-completions endpoint
and run `make live-provider-smoke`.

Each new or changed job version is enriched once. Malformed, uncited, injected,
timed-out, or over-budget responses are retained only as classified
reasoning-run failures; they do not become recommendations or knowledge.

## Build and generated contracts

Build the application images using the deployment values described in [System administration](system-administration.md):

```sh
docker compose build
```

FastAPI’s OpenAPI document is the normative HTTP contract. Regenerate it and the frontend TypeScript types together:

```sh
make generate
```

CI fails when `openapi.json` or `frontend/app/api/schema.d.ts` is stale.

## Local checks

Run formatting, linting, strict Python and TypeScript checks, tests, and generated-contract verification:

```sh
make check
```

Run dependency and secret checks independently when diagnosing CI:

```sh
uv run pip-audit
uv run python scripts/check_secrets.py
cd frontend
corepack npm@10.9.4 audit --audit-level=high
```

The CI Compose job additionally scans the built API and proxy images for high- and critical-severity vulnerabilities.

## Knowledge graph and artifact imports

Milestone 2 supports UTF-8 text and extractable-text PDF CV imports. PDF text
is normalized into stable page/line evidence locators. Explicit skills,
experience, education, and certification sections become pending proposals;
scanned or password-protected PDFs are rejected safely. Create a local Fernet
key and set `CAREER_SECURITY_ARTIFACT_KEY_FILE` in `.env`; the key is required
to encrypt artifacts and evidence before they are stored. An import that
contains no supported sections completes with a visible “No reviewable facts
were found” result rather than silently appearing successful.

The knowledge profile, bounded search/traversal, read-only graph, imports, and
graph history are available from the member navigation. Profile data is scoped
to the authenticated session; source and normalized job data remains shared.
The Reviews page lists pending proposals with encrypted evidence excerpts and
supports explicit approve, edit-and-approve, reject, and date-based defer
decisions. Decisions use the displayed graph version and preserve local edits
when a concurrent change returns `412 GRAPH_VERSION_MISMATCH`.

To reprocess imports created by an older artifact processor, run:

```sh
docker compose run --rm api career artifacts reprocess
```

Reprocessing preserves the existing proposal and decision history and creates
new reviewable results for the updated processor. Review or repair any older
incorrectly classified proposal after the new import completes.

## Full-stack smoke test

Configure a loopback or private address and test certificate as described in [System administration](system-administration.md), then run:

```sh
make compose-smoke
```

The smoke test expects a fresh database because it verifies first-administrator bootstrap. It exercises forced password change, authenticated readiness, account creation and reset, disablement, session revocation, final-administrator protection, logout, and SPA delivery over HTTPS.

It also applies a test-only approved manual-source policy, proves repeat imports are idempotent, appends one changed job version, exercises job/source reads, and verifies feedback is isolated between two profiles. Live feed canaries remain opt-in and require a current source-policy review.

The smoke stack contains known test credentials. Stop it and delete its test-only volumes after verification:

```sh
docker compose down -v
```
