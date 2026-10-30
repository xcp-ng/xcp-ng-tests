import pytest

import json

from data import TOOLS
from lib.commands import local_cmd
from lib.common import wait_for
from lib.typing import JSONType

from typing import Literal, overload

__allow_xo_cli = False
def _allow_xo_cli(value: bool) -> bool:
    """
    Permit to configure the usage of xo_cli function (returns the previous value).
    This function shouldn't be called directly.
    If you need xo_cli(), use the hosts_with_xo fixture.
    """
    global __allow_xo_cli

    old = __allow_xo_cli
    __allow_xo_cli = value

    return old

@overload
def xo_cli(action: str, args: dict[str, str] = ..., *, check: bool = ..., use_json: Literal[False] = ...) -> str:
    ...
@overload
def xo_cli(action: str, args: dict[str, str] = ..., *, check: bool = ..., use_json: Literal[True]) -> JSONType:
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

def xo_rest_action(
    path: str, args: dict[str, str] = {}, *, timeout_secs: int, retry_delay_secs: int = 10
) -> dict[str, JSONType]:
    """
    Start an action through the XO REST API, e.g. `pools/<uuid>/actions/rolling_update`, and return its finished task.

    The task is polled with short requests rather than waited for on a single connection: xo-cli never checks its
    connection to XO, and would wait forever for the result if that connection were lost during a long action.
    """
    if not __allow_xo_cli:
        pytest.fail("xo_rest_action function requires hosts_with_xo fixture usage.")

    def rest(*rest_args: str) -> dict[str, JSONType]:
        res = local_cmd([TOOLS.get('xo-cli', 'xo-cli'), 'rest', '--json', *rest_args])
        result = json.loads(res.stdout)
        assert isinstance(result, dict)
        return result

    task_path = 'tasks/%s' % rest('post', path, *("%s=%s" % (key, value) for key, value in args.items()))['taskId']
    wait_for(lambda: rest('get', task_path)['status'] != 'pending', "Waiting for XO task %s" % task_path,
             timeout_secs=timeout_secs, retry_delay_secs=retry_delay_secs)
    return rest('get', task_path)

def xo_object_exists(uuid: str) -> bool:
    lst = xo_cli('list-objects', {'uuid': uuid}, use_json=True)
    assert isinstance(lst, list)
    return len(lst) > 0
