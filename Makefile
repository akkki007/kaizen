.PHONY: up down install lint test api ui eval

up:        ## start FalkorDB, Postgres, Redis, API
	docker compose up -d --build
down:
	docker compose down
install:
	cd core && uv sync
	cd ui && npm install
lint:
	cd core && uv run ruff check . && uv run mypy src
	cd ui && npm run lint
test:
	cd core && uv run pytest
api:       ## run API locally with reload
	cd core && uv run uvicorn causaldeploy.api.main:app --reload --port 8000
ui:
	cd ui && npm run dev
eval:
	@echo "eval harness not implemented yet (see issue #92)"
