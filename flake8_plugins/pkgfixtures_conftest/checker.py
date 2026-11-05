from __future__ import annotations

import ast
import os

MSG = "PKF001 pkgfixtures must be imported in a conftest.py to be package-scoped"


class Plugin:
    name = "flake8-pkgfixtures-conftest"
    version = "0.1.0"

    def __init__(self, tree: ast.Module, filename: str) -> None:
        self._tree = tree
        self._filename = filename

    def run(self) -> list[tuple[int, int, str, type[Plugin]]]:
        if os.path.basename(self._filename) == "conftest.py":
            return []
        return [
            (node.lineno, node.col_offset, MSG, type(self))
            for node in ast.walk(self._tree)
            if (isinstance(node, ast.ImportFrom) and node.module == "pkgfixtures")
            or (isinstance(node, ast.Import) and any(alias.name == "pkgfixtures" for alias in node.names))
        ]
