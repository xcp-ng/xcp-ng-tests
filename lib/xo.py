import pytest

import json

from data import TOOLS
from lib.commands import local_cmd
from lib.typing import JSONType

from typing import Literal, overload

__allow_xo_cli = False
def _allow_xo_cli(value: bool) -> bool:
    """
    Permit to configure the usage of xo_cli function (returns the previous value).
    This function shoudln't be called directly.
    If you need xo_cli(), use the hosts_with_xo fixture.
    """
    global __allow_xo_cli

    old = __allow_xo_cli
    __allow_xo_cli = value

    return old

@overload
def xo_cli(action: str, args: dict[str, str] = {}, *, check: bool = True, use_json: Literal[False] = False) -> str:
    ...
@overload
def xo_cli(action: str, args: dict[str, str] = {}, *, check: bool = True, use_json: Literal[True]) -> JSONType:
    ...

def xo_cli(action: str, args: dict[str, str] = {}, *, check: bool = True, use_json: bool = False) -> JSONType | str:
    if not __allow_xo_cli:
        pytest.fail("xo_cli function requires hosts_with_xo fixture usage.")

    cmd = [TOOLS.get('xo-cli', 'xo-cli'), action]
    if action != 'list-objects' and use_json:
        cmd += ['--json']
    cmd += ["%s=%s" % (key, value) for key, value in args.items()]

    res = local_cmd(cmd, check=check)

    if use_json:
        return json.loads(res.stdout)

    return res.stdout

def xo_object_exists(uuid: str) -> bool:
    lst = xo_cli('list-objects', {'uuid': uuid}, use_json=True)
    assert isinstance(lst, list)
    return len(lst) > 0
