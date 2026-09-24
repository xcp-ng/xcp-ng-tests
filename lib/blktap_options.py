from __future__ import annotations

import textwrap
import types

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from typing import Any, Literal, Union, get_args, get_origin

# Values of the --blktap option: --blktap KEY:VALUE, repeated, or --blktap KEY:VALUE,KEY:VALUE.
# --blktap help lists the keys.


class BlktapOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_duration: int = Field(
        default=30, description="Maximum duration, in seconds, of the long running stress tests.")


def _type_name(annotation: object) -> str:
    if get_origin(annotation) in (Union, types.UnionType):
        annotation = next(a for a in get_args(annotation) if a is not type(None))
    if get_origin(annotation) is Literal:
        return "|".join(str(value) for value in get_args(annotation))
    return str(getattr(annotation, "__name__", annotation))


def _key(name: str) -> str:
    return name.replace("_", "-")


def blktap_options_help() -> str:
    lines = ["Options of the blktap tests: --blktap KEY:VALUE, repeated or separated by commas",
             "(--blktap KEY:VALUE --blktap KEY:VALUE, or --blktap KEY:VALUE,KEY:VALUE);",
             ""]
    for name, field in BlktapOptions.model_fields.items():
        default = "" if field.default is None else f" (default: {field.default})"
        lines.append(f"  {_key(name)}: {_type_name(field.annotation)}{default}")
        lines.extend(textwrap.wrap(field.description or "", width=100, initial_indent="      ",
                                   subsequent_indent="      "))
    return "\n".join(lines)


def parse_blktap_options(arguments: list[str]) -> BlktapOptions:
    """Build the options from the values of --blktap, raise ValueError if one is wrong."""
    fields = BlktapOptions.model_fields
    values: dict[str, Any] = {}
    for argument in arguments:
        for item in argument.split(","):
            key, separator, value = item.partition(":")
            name = key.strip().replace("-", "_")
            value = value.strip()
            if not separator or name not in fields:
                raise ValueError(
                    f"--blktap {item!r}: expected KEY:VALUE, with KEY among "
                    f"{', '.join(_key(n) for n in fields)} (--blktap help describes them)")
            if name in values:
                raise ValueError(f"--blktap {key.strip()} given twice")
            values[name] = value
    try:
        return BlktapOptions(**values)
    except ValidationError as e:
        problems = "; ".join(f"{_key(str(error['loc'][0]))}: {error['msg']}" for error in e.errors())
        raise ValueError(f"--blktap: {problems}") from e
