# Makefile to run static code checkers locally
# Equivalent to the GitHub Actions workflow in .github/workflows/code-checkers.yml

.PHONY: all mypy pyright ruff ruff-fix flake8 autopep8 autopep8-fix

# By default, only run the non-fix version of the hooks so it doesn't modify any files
# It runs on all files managed by git (untracked files are not checked)
check: ruff autopep8 flake8 mypy pyright

# Use the `fix` directive to let autopep8 auto-format the code and ruff sort the imports
# It runs on all files managed by git (untracked files are not modified)
fix: ruff-fix autopep8-fix

data.py:
	@test -r data.py || echo "File 'data.py' does not exist. Refer to https://github.com/xcp-ng/xcp-ng-tests#configuration." && exit 1

mypy pyright ruff: data.py

ruff ruff-fix autopep8 autopep8-fix flake8 mypy pyright:
	uv run prek -a $@
