from __future__ import annotations

import ast
import textwrap

from pkgfixtures_conftest.checker import Plugin

def check(code: str, filename: str) -> list[str]:
    tree = ast.parse(textwrap.dedent(code))
    return [msg for _, _, msg, _ in Plugin(tree, filename).run()]


CODE = """
import pkgfixtures
from pkgfixtures import host_with_saved_yum_state
from lib.host import Host
"""


def test_conftest_allowed() -> None:
    assert check(CODE, "tests/storage/zfs/conftest.py") == []


def test_test_module_flagged() -> None:
    assert check(CODE, "tests/storage/misc/test_http_nbd_transfer.py") == [
        "PKF001 pkgfixtures must be imported in a conftest.py to be package-scoped",
        "PKF001 pkgfixtures must be imported in a conftest.py to be package-scoped",
    ]


def test_other_imports_ignored() -> None:
    assert check("from lib.pkgfixtures import x\nimport pkgfixtures_other\n", "tests/test_x.py") == []
