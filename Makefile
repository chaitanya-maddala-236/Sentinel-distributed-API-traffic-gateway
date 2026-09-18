.PHONY: up down logs test test-unit lint fmt migrate demo-kill demo-restore

up:
	docker compose up --build

down:
	docker compose down -v

logs:
	docker compose logs -f gateway-1 gateway-2 control-plane

test-unit:
	pytest tests/unit -v

test:
	pytest tests/ -v

lint:
	ruff check .
	mypy apps packages

fmt:
	ruff format .

migrate:
	cd apps/control_plane && alembic upgrade head

demo-kill:
	curl -s -X POST http://localhost:9001/admin/kill

demo-restore:
	curl -s -X POST http://localhost:9001/admin/restore

k6-baseline:
	k6 run tests/load/baseline.js

k6-stress:
	k6 run tests/load/stress.js
