.PHONY: install lint format test migrate run compose-up compose-down sdk-build

install:
	python -m pip install -e ".[dev]"
	python -m pip install -e sdk/python

lint:
	ruff check .
	ruff format --check .

format:
	ruff check --fix .
	ruff format .

test:
	pytest

migrate:
	alembic -c backend/alembic.ini upgrade head

run:
	uvicorn backend.app.main:app --reload

compose-up:
	docker compose up --build

compose-down:
	docker compose down

sdk-build:
	python -m build sdk/python

