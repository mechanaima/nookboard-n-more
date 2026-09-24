.PHONY: dev test test-js check-render check shot clean

dev:
	uv run uvicorn app.main:app --reload --port 8765 --host 127.0.0.1

test:
	uv run pytest -x

test-js:
	npm test

# Loads the app in headless Chromium and asserts the front end actually painted.
# Requires the server to be running (make dev).
check-render:
	./tools/check_render.sh

# Everything. Run before calling a UI change done.
check: test test-js
	@curl -sf http://127.0.0.1:8765/api/health >/dev/null \
		|| { echo "!! server not running — start it with 'make dev' first"; exit 1; }
	./tools/check_render.sh

shot:
	./tools/shot.sh /tmp/nookboard.png

# Deliberately does NOT touch vault/ — that's your data.
clean:
	rm -rf .pytest_cache __pycache__ app/__pycache__ tests/__pycache__
