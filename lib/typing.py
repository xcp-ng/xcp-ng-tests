import sys

from typing import NotRequired, TypedDict

if sys.version_info >= (3, 11):
    from typing import NotRequired
else:
    from typing_extensions import NotRequired


VmDef = str | tuple[str, str]


class VMSDef(TypedDict):
    single: dict[str, VmDef]
    multi: dict[str, list[VmDef]]


IsoImageDef = TypedDict('IsoImageDef',
                        {'path': str,
                         'net-url': NotRequired[str],
                         'net-only': NotRequired[bool],
                         'unsigned': NotRequired[bool],
                         })

JSONType = None | bool | int | float | str | list["JSONType"] | dict[str, "JSONType"]
