# Makefile to run static code checkers locally
# Equivalent to the GitHub Actions workflow in .github/workflows/code-checkers.yml

.PHONY: all mypy pyright ruff flake8

all: ruff autopep8 flake8 mypy pyright

data.py:
	@test -r data.py || echo "File 'data.py' does not exist. Refer to https://github.com/xcp-ng/xcp-ng-tests#configuration." && exit 1

mypy pyright ruff: data.py

ruff autopep8 flake8 mypy pyright:
	uv run prek -a $@
