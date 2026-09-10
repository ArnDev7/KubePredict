.PHONY: test lint format-check type-check

test:
	python -m pytest

lint:
	python -m ruff check .

format-check:
	python -m ruff format --check .

type-check:
	python -m mypy app controller forecasting config shared
