# Makefile to run static code checkers locally
# Equivalent to the GitHub Actions workflow in .github/workflows/code-checkers.yml

.PHONY: all mypy pyright ruff flake8

all: ruff autopep8 flake8 mypy pyright

data.py:
	@test -r data.py || echo "File 'data.py' does not exist. Refer to https://github.com/xcp-ng/xcp-ng-tests#configuration." && exit 1

mypy: data.py
	uv run prek -a mypy

pyright: data.py
	uv run prek -a pyright

ruff: data.py
	uv run prek -a ruff

flake8:
	uv run prek -a flake8

autopep8:
	uv run prek -a autopep8
