"""Build the editor-oriented JSON schema for the config files.

``config-schema.json`` is derived from the pydantic Config model, but unlike
pydantic's default output it:

- marks every field optional (no ``required`` arrays): overlay files only carry
  the keys they override, so a fully-specified required schema would flag valid
  partial configs,
- rejects unknown keys (``additionalProperties: false``),
- documents loader-level pseudo-properties that are not model fields (see
  ``_EXTRA_PROPERTIES`` below).
"""
from __future__ import annotations

from lib.config_loader import Config

from typing import Any

# Loader-level pseudo-properties (not pydantic model fields) that config files
# may contain. Each value is merged verbatim into the top-level "properties".
_EXTRA_PROPERTIES: dict[str, Any] = {
    "$schema": {
        "type": "string",
        "description": "Path to the JSON schema used by the editor for validation and autocompletion.",
    },
    "include": {
        "type": "array",
        "items": {"type": "string"},
        "description": "List of TOML files to load and deep-merge before this file's content. "
        "Paths are relative to this file's directory.",
        "default": [],
    },
}

# Config fields that accept a human-readable size string ("1 GiB") in addition
# to a plain integer, normalized to an int by the model's parse_size_str
# validator. The pydantic model types them as int, so the schema needs the
# string alternative added by hand.
_SIZE_FIELDS = {"volume_size", "write_volume_cap"}


def editor_schema() -> dict[str, Any]:
    """Return the editor-oriented schema for the current Config model."""
    schema = Config.model_json_schema()

    def postprocess(obj: object) -> None:
        if isinstance(obj, dict):
            obj.pop("required", None)
            if obj.get("type") == "object":
                obj.setdefault("additionalProperties", False)
            for value in obj.values():
                postprocess(value)
        elif isinstance(obj, list):
            for value in obj:
                postprocess(value)

    postprocess(schema)
    for field in _SIZE_FIELDS & set(schema["properties"]):
        prop = schema["properties"][field]
        prop["anyOf"] = [{"type": "integer"}, {"type": "string"}]
        prop.pop("type", None)
    schema["title"] = "XCP-ng Tests Configuration Schema"
    schema["description"] = (
        "JSON Schema for validating config.toml, config.default.toml, and config.NAME.toml files. "
        "Properties are all optional since overlay files only carry the keys they override; "
        "the base config.toml provides the rest."
    )
    schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    schema.setdefault("additionalProperties", False)
    schema["properties"] = {**_EXTRA_PROPERTIES, **schema.get("properties", {})}
    return schema
