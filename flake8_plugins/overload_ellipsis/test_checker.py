from __future__ import annotations

import ast
import textwrap

from overload_ellipsis.checker import Plugin

def check(code: str) -> list[str]:
    tree = ast.parse(textwrap.dedent(code))
    return [msg for _, _, msg, _ in Plugin(tree).run()]


def test_ellipsis_defaults_allowed() -> None:
    code = """
    from typing import overload

    @overload
    def f(x: int = ..., *, y: str = ...) -> None: ...

    @typing.overload
    def f(x: int = ...) -> None: ...

    @overload()
    def f() -> None: ...

    def f(x: int = 0, *, y: str = "") -> None:  # implementation is exempt
        return
    """
    assert check(code) == []


def test_flagged_defaults() -> None:
    code = """
    from typing import overload

    @overload
    def f(x: int = 1, y: int = None, *, z: int = 2) -> None: ...
    """
    assert check(code) == [
        "OVE001 default value in @overload signature must be ... for 'x'",
        "OVE001 default value in @overload signature must be ... for 'y'",
        "OVE001 default value in @overload signature must be ... for 'z'",
    ]


def test_regular_functions_ignored() -> None:
    code = """
    def f(x: int = 1) -> None: ...
    """
    assert check(code) == []
