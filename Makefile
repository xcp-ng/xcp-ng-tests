# Makefile to run static code checkers locally
# Equivalent to the GitHub Actions workflow in .github/workflows/code-checkers.yml

.PHONY: all mypy pyright ruff flake8

all: mypy pyright ruff flake8

data.py:
	@test -r data.py || echo "File 'data.py' does not exist. Refer to https://github.com/xcp-ng/xcp-ng-tests#configuration." && exit 1

mypy: data.py
	uv run mypy --install-types --non-interactive lib/ conftest.py pkgfixtures.py tests/

pyright: data.py
	uv run pyright lib/ conftest.py pkgfixtures.py

ruff: data.py
	FORCE_COLOR=1 uv run ruff check lib/ tests/

flake8:
	uv run flake8
