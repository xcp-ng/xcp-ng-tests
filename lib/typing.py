from typing import TypedDict
from typing_extensions import NotRequired

IsoImageDef = TypedDict('IsoImageDef',
                        {'path': str,
                         'net-url': NotRequired[str],
                         'net-only': NotRequired[bool],
                         'unsigned': NotRequired[bool],
                         })
