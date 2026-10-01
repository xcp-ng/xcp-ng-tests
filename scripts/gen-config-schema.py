"""Regenerate config-schema.json from the pydantic Config model.

Run from the repository root:
    uv run scripts/gen-config-schema.py
"""
import json
import sys
from pathlib import Path

# Add root project directory into PYTHONPATH
sys.path.append(str(Path(__file__).absolute().parent.parent))

# flake8: noqa: E402 module level import not at top of file
from lib.config_schema import editor_schema

if __name__ == "__main__":
    out = Path(__file__).absolute().parent.parent / "config-schema.json"
    out.write_text(json.dumps(editor_schema(), indent=2, sort_keys=True) + "\n")
    print(f"Wrote {out}")
