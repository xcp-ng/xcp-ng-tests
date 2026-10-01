import sys

if sys.version_info >= (3, 12):
    from typing import TypeAliasType
else:
    from typing_extensions import TypeAliasType

JSONType = TypeAliasType("JSONType", None | bool | int | float | str | list["JSONType"] | dict[str, "JSONType"])

ConfigDict = dict[str, JSONType]
