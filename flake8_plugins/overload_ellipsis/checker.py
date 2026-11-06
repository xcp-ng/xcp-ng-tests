from __future__ import annotations

import ast

MSG = "OVE001 default value in @overload signature must be ..."


def _is_overload(decorator: ast.expr) -> bool:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    if isinstance(target, ast.Attribute):
        return target.attr == "overload"
    return isinstance(target, ast.Name) and target.id == "overload"


class Plugin:
    name = "flake8-overload-ellipsis"
    version = "0.1.0"

    def __init__(self, tree: ast.AST) -> None:
        self._tree = tree

    def run(self) -> list[tuple[int, int, str, type[Plugin]]]:
        errors = []
        for node in ast.walk(self._tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not any(_is_overload(dec) for dec in node.decorator_list):
                continue
            args = node.args
            positional = [*args.posonlyargs, *args.args]
            defaulted = positional[-len(args.defaults):] if args.defaults else []
            kwonly = [(arg, dflt) for arg, dflt in zip(args.kwonlyargs, args.kw_defaults) if dflt is not None]
            for arg, dflt in [*zip(defaulted, args.defaults), *kwonly]:
                if not (isinstance(dflt, ast.Constant) and dflt.value is Ellipsis):
                    name = f" for {arg.arg!r}"
                    errors.append((node.lineno, node.col_offset, MSG + name, type(self)))
        return errors
