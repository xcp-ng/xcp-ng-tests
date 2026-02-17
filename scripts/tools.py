"""Just a wrapper to run cli tools package.
"""
import sys
from pathlib import Path

# Add root project directory into PYTHONPATH
sys.path.append(str(Path(__file__).absolute().parent.parent))

# flake8: noqa: E402 module level import not at top of file
from lib.tools.cli import cli

if __name__ == "__main__":
    cli()
