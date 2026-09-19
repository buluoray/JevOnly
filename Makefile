.PHONY: install test lint fmt serve clean

install:
	python3 -m pip install -e '.[dev]'
	npm install --registry=https://registry.npmjs.org/
	playwright install chromium

test:
	pytest -q
	npm test

lint:
	ruff check .
	ruff format --check .
	npx prettier --check .
	npm run lint

fmt:
	ruff check --fix .
	ruff format .
	npx prettier --write .

serve:
	jevonly serve

clean:
	rm -rf build dist .pytest_cache .ruff_cache
	find src tests -type d -name __pycache__ -prune -exec rm -rf {} +
	find src tests -type f -name '*.py[co]' -delete
