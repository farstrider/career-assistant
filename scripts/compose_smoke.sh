#!/bin/sh
set -eu

output="$(docker compose run --rm api career auth bootstrap-admin \
  --username smoke-admin --display-name 'Smoke Administrator')"
password="${output#*Temporary password (shown once): }"
if [ "$password" = "$output" ] || [ -z "$password" ]; then
  echo "administrator bootstrap did not return a temporary password" >&2
  exit 1
fi

fixtures="$(pwd)/tests/fixtures"
docker compose run --rm -v "$fixtures:/fixtures:ro" api \
  career sources apply-policy /fixtures/source-policy.yaml
docker compose run --rm -v "$fixtures:/fixtures:ro" api \
  career sources import smoke-manual /fixtures/jobs-v1.json
docker compose run --rm -v "$fixtures:/fixtures:ro" api \
  career sources import smoke-manual /fixtures/jobs-v1.json
docker compose run --rm -v "$fixtures:/fixtures:ro" api \
  career sources import smoke-manual /fixtures/jobs-v2.json
docker compose run --rm -v "$fixtures:/fixtures:ro" api \
  career sources import smoke-manual /fixtures/jobs-v3.json
if docker compose run --rm -v "$fixtures:/fixtures:ro" api \
  career sources import smoke-manual /fixtures/jobs-invalid.json; then
  echo "invalid manual import unexpectedly succeeded" >&2
  exit 1
fi

base_url="${CAREER_SMOKE_BASE_URL:-${CAREER_APP_BASE_URL:-}}"
if [ -z "$base_url" ]; then
  base_url="$(docker compose config --environment | awk '$1 == "CAREER_APP_BASE_URL" {print $2}')"
fi
CAREER_APP_BASE_URL="$base_url" CAREER_SMOKE_PASSWORD="$password" python3 scripts/compose_smoke.py
