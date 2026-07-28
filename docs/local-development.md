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

The default tests use no external provider, source content, or private career data.

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

## Full-stack smoke test

Configure a private address and test certificate as described in [System administration](system-administration.md), then run:

```sh
make compose-smoke
```

The smoke test expects a fresh database because it verifies first-administrator bootstrap. It exercises forced password change, authenticated readiness, account creation and reset, disablement, session revocation, final-administrator protection, logout, and SPA delivery over HTTPS.

The smoke stack contains known test credentials. Stop it and delete its test-only volumes after verification:

```sh
docker compose down -v
```
