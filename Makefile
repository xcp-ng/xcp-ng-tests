# Makefile to run static code checkers locally
# Equivalent to the GitHub Actions workflow in .github/workflows/code-checkers.yml

.PHONY: all mypy pyright ruff flake8

all: mypy pyright ruff flake8

mypy:
	uv run mypy --install-types --non-interactive .

pyright:
	uv run pyright .

ruff:
	FORCE_COLOR=1 uv run ruff check

flake8:
	uv run flake8
