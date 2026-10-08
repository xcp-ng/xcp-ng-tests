from __future__ import annotations

import ast
import textwrap

from type_checking_block.checker import Plugin

def check(code: str) -> list[str]:
    tree = ast.parse(textwrap.dedent(code))
    return [msg for _, _, msg, _ in Plugin(tree).run()]


def test_imports_after_type_checking_block_flagged() -> None:
    code = """
    from typing import TYPE_CHECKING

    if TYPE_CHECKING:
        from lib.host import Host

    from lib.common import f
    from lib.sr import SR
    """
    assert check(code) == [
        "TCB001 import statement after the TYPE_CHECKING block",
        "TCB001 import statement after the TYPE_CHECKING block",
    ]


def test_imports_before_type_checking_block_allowed() -> None:
    code = """
    from typing import TYPE_CHECKING

    from lib.common import f

    if TYPE_CHECKING:
        from lib.host import Host
    """
    assert check(code) == []


def test_typing_type_checking_attribute_form() -> None:
    code = """
    if typing.TYPE_CHECKING:
        from lib.host import Host

    from lib.common import f
    """
    assert check(code) == [
        "TCB001 import statement after the TYPE_CHECKING block",
    ]


def test_regular_code_after_block_stops_checking() -> None:
    code = """
    from typing import TYPE_CHECKING

    if TYPE_CHECKING:
        from lib.host import Host

    def f() -> None:
        import os
    """
    assert check(code) == []


def test_regular_if_block_ignored() -> None:
    code = """
    if os.name == "posix":
        import posix

    import fcntl
    """
    assert check(code) == []


def test_regular_code_between_imports_does_not_rearm() -> None:
    code = """
    from typing import TYPE_CHECKING

    if TYPE_CHECKING:
        from lib.host import Host

    from lib.common import f

    def f() -> None:
        return

    import os
    """
    assert check(code) == [
        "TCB001 import statement after the TYPE_CHECKING block",
    ]
