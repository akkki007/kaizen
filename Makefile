.PHONY: mcp env backfill bootstrap dev deps up down install lint test migrate schemas api worker ui eval seed \
        backup restore drill

env:         ## .env from the team GitHub App in AWS Secrets Manager (needs AWS credentials)
	./scripts/pull-env.sh

backfill:    ## import the team repo into the local graph: make backfill [REPO=owner/name]
	cd core && uv run python -m causaldeploy.cli backfill $(or $(REPO),yashchandnani07/shoplite)

bootstrap:   ## first-time setup on a fresh clone: .env, services, deps, database schema
	@test -f .env || cp .env.example .env
	docker compose up -d --wait falkordb postgres redis
	cd core && uv sync && uv run alembic upgrade head
	cd ui && npm install
	@echo "ready: make test | make api | make worker | make ui"

dev:         ## everything local: data services, API :8000, worker, UI :3000 on demo data (MOCK=0: real API)
	MOCK=$(MOCK) ./scripts/dev.sh

deps:        ## start only the data services (what tests need)
	docker compose up -d --wait falkordb postgres redis

up:          ## whole stack in containers (API on :$${CD_API_PORT:-8000})
	@test -f .env || cp .env.example .env
	docker compose up -d --build --wait

down:
	docker compose down

install:
	cd core && uv sync
	cd ui && npm install

lint:
	cd core && uv run ruff check . && uv run mypy src
	cd ui && npm run lint

test: deps   ## backend tests against the real services
	cd core && uv run pytest

migrate:
	cd core && uv run alembic upgrade head

schemas:     ## regenerate docs/schemas and docs/openapi.json after a contract change
	cd core && uv run python -m causaldeploy.cli export-schemas

api:         ## API locally with reload
	cd core && uv run uvicorn causaldeploy.api.main:app --reload --port 8000

mcp:         ## graph tools over MCP (stdio): make mcp [REPO=owner/name]
	cd core && uv run python -m causaldeploy.mcp_server --repo $(or $(REPO),yashchandnani07/shoplite)

worker:
	cd core && uv run arq causaldeploy.jobs.worker.WorkerSettings

ui:
	cd ui && npm run dev

backup:      ## Postgres + FalkorDB snapshot into ./backups (COMPOSE_FILE=... for prod)
	./scripts/backup.sh

restore:     ## make restore DIR=backups/<timestamp>
	./scripts/restore.sh $(DIR)

drill:       ## backup -> destroy -> restore -> verify, on a throwaway project
	./scripts/test-restore.sh

eval:        ## graph vs. baselines on the seeded scenarios -> docs/results/latest.{md,json,html}
	cd core && uv run python -m causaldeploy.evalharness --jobs 4 --quiet --out ../docs/results/latest.md

.PHONY: eval-live
eval-live:   ## agent golden tests (S1-S5 rubric) against the REAL model; needs CD_OPENAI_API_KEY + CD_OPENAI_MODEL, costs money; replies recorded to eval/llm-live
	cd core && uv run python -m causaldeploy.evalharness.llm_golden --mode live --dir ../eval/llm-live

seed:
	@echo "ShopLite lives in https://github.com/yashchandnani07/shoplite (history seeded, see docs/history.md there)"
