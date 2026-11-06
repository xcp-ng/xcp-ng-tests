from __future__ import annotations

import ast

MSG = "TCB001 import statement after the TYPE_CHECKING block"


def _is_type_checking(test: ast.expr) -> bool:
    target = test.func if isinstance(test, ast.Call) else test
    if isinstance(target, ast.Attribute):
        return target.attr == "TYPE_CHECKING"
    return isinstance(target, ast.Name) and target.id == "TYPE_CHECKING"


class Plugin:
    name = "flake8-type-checking-block"
    version = "0.1.0"

    def __init__(self, tree: ast.Module) -> None:
        self._tree = tree

    def run(self) -> list[tuple[int, int, str, type[Plugin]]]:
        errors = []
        seen_type_checking = False
        for node in self._tree.body:
            if isinstance(node, ast.If) and _is_type_checking(node.test):
                seen_type_checking = True
                continue
            if seen_type_checking and isinstance(node, (ast.Import, ast.ImportFrom)):
                errors.append((node.lineno, node.col_offset, MSG, type(self)))
            elif not isinstance(node, (ast.Import, ast.ImportFrom, ast.Expr)):
                break
        return errors
