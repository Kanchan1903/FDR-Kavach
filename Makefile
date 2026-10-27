.PHONY: dev up down migrate seed test lint frontend-build
dev:
	docker compose up --build
up:
	docker compose up --build
down:
	docker compose down
migrate:
	docker compose run --rm api python manage.py migrate
test:
	docker compose run --rm api sh -c "coverage run --source=security.coverage,security.reconciliation,security.record_services manage.py test && coverage report --fail-under=85"
seed:
	docker compose run --rm api sh -c "python manage.py migrate --noinput && python manage.py seed_demo"
lint:
	cd backend && ruff check .
frontend-build:
	cd frontend && npm run build
