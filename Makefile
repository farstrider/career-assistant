.PHONY: bootstrap-admin check compose-smoke generate live-provider-smoke openapi test up

export UV_CACHE_DIR ?= .uv-cache

up:
	docker compose up --build --wait

bootstrap-admin:
	docker compose run --rm api career auth bootstrap-admin

openapi:
	uv run python scripts/export_openapi.py

generate: openapi
	cd frontend && corepack npm@10.9.4 exec -- openapi-typescript ../openapi.json -o app/api/schema.d.ts

test:
	uv run pytest
	cd frontend && corepack npm@10.9.4 test

check: generate
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy
	uv run pytest
	cd frontend && corepack npm@10.9.4 run format
	cd frontend && corepack npm@10.9.4 run typecheck
	cd frontend && corepack npm@10.9.4 test
	git diff --exit-code -- openapi.json frontend/app/api/schema.d.ts

compose-smoke: up
	./scripts/compose_smoke.sh

live-provider-smoke:
	uv run python scripts/live_provider_smoke.py
