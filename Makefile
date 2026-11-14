# Convenience targets for local development (Windows users can run the commands
# directly; Docker Compose targets work from any shell).

.PHONY: up down logs backend worker beat test migrate seed dev backend-eval frontend-install frontend-dev frontend-check

up:
	docker compose up --build

dev:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build

down:
	docker compose down

logs:
	docker compose logs -f backend celery-worker frontend

backend:
	docker compose up -d postgres redis
	cd backend && uvicorn app.main:app --reload

worker:
	cd backend && celery -A app.worker.celery_app worker --loglevel=info

beat:
	cd backend && celery -A app.worker.celery_app beat --loglevel=info

test:
	cd backend && pytest

migrate:
	cd backend && alembic upgrade head

seed:
	docker compose exec backend python -m scripts.seed_data

# Optional judge-model evaluators (Ragas/DeepEval). The custom evaluator works
# without this; installing pulls large transitive dependencies and bills judge calls.
backend-eval:
	cd backend && pip install -r requirements-eval.txt

frontend-install:
	cd frontend && npm ci

frontend-dev:
	cd frontend && npm run dev

# Type-check, lint and production-build the dashboard.
frontend-check:
	cd frontend && npm run typecheck && npm run lint && npm run build
