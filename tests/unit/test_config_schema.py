from __future__ import annotations

import json
import tomllib
from pathlib import Path

from lib.config_loader import Config
from lib.config_schema import editor_schema
from lib.typing import JSONType

_SCHEMA_PATH = Path(__file__).parents[2] / "config-schema.json"


def _schema() -> dict[str, JSONType]:
    return json.loads(_SCHEMA_PATH.read_text())


def _collect_refs(obj: JSONType, refs: list[str]) -> None:
    if isinstance(obj, dict):
        if "$ref" in obj and isinstance(obj["$ref"], str):
            refs.append(obj["$ref"])
        for value in obj.values():
            _collect_refs(value, refs)
    elif isinstance(obj, list):
        for value in obj:
            _collect_refs(value, refs)


def _validate_value(value: JSONType, schema: JSONType, defs: dict[str, JSONType], path: str = "$") -> None:
    """Assert ``value`` matches ``schema``, for the subset of JSON Schema we generate."""
    if not isinstance(schema, dict):
        return
    ref = schema.get("$ref")
    if isinstance(ref, str):
        _validate_value(value, defs[ref[len("#/$defs/"):]], defs, path)
        return
    any_of = schema.get("anyOf")
    if isinstance(any_of, list):
        for branch in any_of:
            try:
                _validate_value(value, branch, defs, path)
                return
            except AssertionError:
                pass
        raise AssertionError(f"{path}: {value!r} matches none of {any_of!r}")
    expected = schema.get("type")
    if expected == "integer":
        assert isinstance(value, int) and not isinstance(value, bool), f"{path}: {value!r} is not an integer"
    elif expected == "string":
        assert isinstance(value, str), f"{path}: {value!r} is not a string"
    elif expected == "boolean":
        assert isinstance(value, bool), f"{path}: {value!r} is not a boolean"
    elif expected == "null":
        assert value is None, f"{path}: {value!r} is not null"
    elif expected == "array":
        assert isinstance(value, list), f"{path}: {value!r} is not an array"
        items = schema.get("items", {})
        for i, item in enumerate(value):
            _validate_value(item, items, defs, f"{path}[{i}]")
    elif expected == "object":
        assert isinstance(value, dict), f"{path}: {value!r} is not an object"
        properties = schema.get("properties", {})
        assert isinstance(properties, dict)
        additional = schema.get("additionalProperties", True)
        for key, sub in value.items():
            if key in properties:
                _validate_value(sub, properties[key], defs, f"{path}.{key}")
            elif additional is False:
                raise AssertionError(f"{path}: additional property {key!r} is not allowed")
            elif isinstance(additional, dict):
                _validate_value(sub, additional, defs, f"{path}.{key}")


def test_schema_is_valid_json() -> None:
    schema = _schema()
    assert "properties" in schema


def test_schema_covers_all_model_fields() -> None:
    model_props = Config.model_json_schema()["properties"]
    schema_props = _schema()["properties"]
    assert isinstance(schema_props, dict)
    for key in model_props:
        assert key in schema_props, f"config-schema.json is missing model field {key!r}"


def test_schema_defs_match_model() -> None:
    model_defs = Config.model_json_schema()["$defs"]
    schema_defs = _schema().get("$defs", {})
    assert isinstance(schema_defs, dict)
    assert set(schema_defs) == set(model_defs), (
        "config-schema.json $defs are out of sync with the Config model. "
        "Regenerate with: uv run scripts/gen-config-schema.py"
    )


def test_schema_refs_resolve() -> None:
    schema = _schema()
    defs = schema.get("$defs", {})
    assert isinstance(defs, dict)
    refs: list[str] = []
    _collect_refs(schema, refs)
    for ref in refs:
        assert ref.startswith("#/$defs/"), f"unexpected $ref {ref!r}"
        assert ref[len("#/$defs/"):] in defs, f"$ref {ref!r} does not resolve to a $def"


def test_schema_matches_generator() -> None:
    assert _schema() == editor_schema(), (
        "config-schema.json is not what scripts/gen-config-schema.py produces. "
        "Regenerate with: uv run scripts/gen-config-schema.py"
    )


def test_config_toml_validates_against_schema() -> None:
    schema = _schema()
    defs = schema.get("$defs", {})
    assert isinstance(defs, dict)
    config_toml = Path(__file__).parents[2] / "config.toml"
    with open(config_toml, "rb") as f:
        data = tomllib.load(f)
    _validate_value(data, schema, defs)
