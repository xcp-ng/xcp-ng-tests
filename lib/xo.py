import json

from data import TOOLS
from lib.commands import local_cmd
from lib.typing import JSONType

from typing import Literal, overload

@overload
def xo_cli(action: str, args: dict[str, str] = {}, *, check: bool = True, use_json: Literal[False] = False) -> str:
    ...
@overload
def xo_cli(action: str, args: dict[str, str] = {}, *, check: bool = True, use_json: Literal[True]) -> JSONType:
    ...

def xo_cli(action: str, args: dict[str, str] = {}, *, check: bool = True, use_json: bool = False) -> JSONType | str:
    cmd = [TOOLS.get('xo-cli', 'xo-cli'), action]
    if use_json:
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
