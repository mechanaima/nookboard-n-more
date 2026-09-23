.PHONY: dev test test-js clean

dev:
	uv run uvicorn app.main:app --reload --port 8765 --host 127.0.0.1

test:
	uv run pytest -x

test-js:
	npm test

clean:
	rm -rf .pytest_cache vault/* __pycache__ */__pycache__ .venv/bin/activate