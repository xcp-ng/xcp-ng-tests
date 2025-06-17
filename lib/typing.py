from __future__ import annotations

import sys
from typing import Sequence, TypedDict, Union

if sys.version_info >= (3, 11):
    from typing import NotRequired, Self
else:
    from typing_extensions import NotRequired, Self

IsoImageDef = TypedDict('IsoImageDef',
                        {'path': str,
                         'net-url': NotRequired[str],
                         'net-only': NotRequired[bool],
                         'unsigned': NotRequired[bool],
                         })


# Dict-based description of an Answerfile object to be built.
AnswerfileDict = TypedDict('AnswerfileDict', {
    'TAG': str,
    'CONTENTS': Union[str, "list[AnswerfileDict]"],
})

# Simplified version of AnswerfileDict for user input.
# - does not require to write 0 or 1 subelement as a list
SimpleAnswerfileDict = TypedDict('SimpleAnswerfileDict', {
    'TAG': str,
    'CONTENTS': NotRequired[Union[str, "SimpleAnswerfileDict", Sequence["SimpleAnswerfileDict"]]],

    # No way to allow arbitrary fields in addition?  This conveys the
    # field's type, but allows them in places we wouldn't want them,
    # and forces every XML attribute we use to appear here.
    'device': NotRequired[str],
    'guest-storage': NotRequired[str],
    'mode': NotRequired[str],
    'name': NotRequired[str],
    'proto': NotRequired[str],
    'type': NotRequired[str],
})
