"""Render config dicts as TOML text (used by migrate_data_py and dump-config)."""

from __future__ import annotations

import difflib
import json

import tomli_w
from pygments import highlight
from pygments.formatters import TerminalFormatter
from pygments.lexers import TOMLLexer

from lib.typing import ConfigDict, JSONType

from typing import overload

_TOML_LEXER = TOMLLexer()
_TOML_FORMATTER = TerminalFormatter()


def deep_dict_equal(d1: JSONType, d2: JSONType) -> bool:
    """Check if two values are deeply equal."""
    if isinstance(d1, dict) and isinstance(d2, dict):
        if set(d1.keys()) != set(d2.keys()):
            return False
        return all(deep_dict_equal(d1[k], d2[k]) for k in d1)
    if isinstance(d1, list) and isinstance(d2, list):
        return len(d1) == len(d2) and all(deep_dict_equal(a, b) for a, b in zip(d1, d2))
    # For non-container types, check both type and value
    return type(d1) is type(d2) and d1 == d2


@overload
def _strip_password_hashes(obj: ConfigDict) -> ConfigDict:
    ...


@overload
def _strip_password_hashes(obj: JSONType) -> JSONType:
    ...


def _strip_password_hashes(obj: JSONType) -> JSONType:
    """Recursively strip password hashes for comparison purposes.

    - Replaces $6$... hashes with placeholder
    - Converts tuples to lists for consistent comparison
    """
    if isinstance(obj, str):
        if obj.startswith("$6$"):
            return "<PASSWORD_HASH>"
        return obj
    if isinstance(obj, dict):
        return {k: _strip_password_hashes(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_strip_password_hashes(item) for item in obj]
    return obj


def remove_defaults(config: ConfigDict, base: ConfigDict) -> ConfigDict:
    """Remove fields from config that have the same value as in base.

    Ignores password hash differences (strips them for comparison).
    """
    result: ConfigDict = {}

    for key, value in config.items():
        if key not in base:
            # Key not in base, keep it
            result[key] = value
        elif isinstance(value, dict) and isinstance((base_value := base.get(key)), dict):
            # Recursively check nested dicts
            nested = remove_defaults(value, base_value)
            if nested:  # Only add if there's something left
                result[key] = nested
        else:
            # Strip password hashes before comparing
            value_stripped = _strip_password_hashes(value)
            base_stripped = _strip_password_hashes(base.get(key))
            if not deep_dict_equal(value_stripped, base_stripped):
                # Values differ (ignoring password hashes), keep it
                result[key] = value
        # else: values are the same, skip it

    return result


def colorize_toml(text: str) -> str:
    """Apply ANSI syntax coloring to TOML text (for TTY display)."""
    return highlight(text, _TOML_LEXER, _TOML_FORMATTER)


@overload
def _sorted_recursive(value: ConfigDict) -> ConfigDict:
    ...


@overload
def _sorted_recursive(value: JSONType) -> JSONType:
    ...


def _sorted_recursive(value: JSONType) -> JSONType:
    """Recursively sort dict keys and drop None values for stable output."""
    if isinstance(value, dict):
        return {
            k: _sorted_recursive(v) for k, v in sorted(value.items()) if v is not None
        }
    if isinstance(value, list):
        return [_sorted_recursive(item) for item in value if item is not None]
    return value


def render_toml(config: ConfigDict, with_schema: bool = True) -> str:
    """Render config dict to TOML text."""
    data = _sorted_recursive(config)
    if with_schema:
        data = {"$schema": "./config-schema.json", **data}
    return tomli_w.dumps(data, multiline_strings=True)


def config_diff(
    config_a: ConfigDict,
    config_b: ConfigDict,
    name_a: str = "a",
    name_b: str = "b",
    as_json: bool = False,
) -> str:
    """Return a unified diff between two configs, ignoring password hashes.

    Empty string when the configs are identical (modulo $6$... password hashes).
    """
    norm_a = _strip_password_hashes(config_a)
    norm_b = _strip_password_hashes(config_b)
    if as_json:
        text_a = json.dumps(norm_a, indent=2, ensure_ascii=False, sort_keys=True)
        text_b = json.dumps(norm_b, indent=2, ensure_ascii=False, sort_keys=True)
    else:
        text_a = render_toml(norm_a, with_schema=False)
        text_b = render_toml(norm_b, with_schema=False)
    return "".join(difflib.unified_diff(
        text_a.splitlines(keepends=True),
        text_b.splitlines(keepends=True),
        fromfile=name_a, tofile=name_b,
    ))
