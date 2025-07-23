import json
import subprocess

from typing import Any, Dict, Literal, Union, overload

@overload
def xo_cli(action: str, args: Dict[str, str] = {}, *, check: bool = True, simple_output: Literal[True] = True,
           use_json: Literal[False] = False) -> str:
    ...
@overload
def xo_cli(action: str, args: Dict[str, str] = {}, *, check: bool = True, simple_output: Literal[True] = True,
           use_json: Literal[True]) -> Any:
    ...
@overload
def xo_cli(action: str, args: Dict[str, str] = {}, *, check: bool = True, simple_output: Literal[False],
           use_json: bool = False) -> subprocess.CompletedProcess:
    ...
@overload
def xo_cli(action: str, args: Dict[str, str] = {}, *, check: bool = True, simple_output: bool = True,
           use_json: bool = False) -> Union[subprocess.CompletedProcess, Any, str]:
    ...
def xo_cli(action, args={}, check=True, simple_output=True, use_json=False):
    run_array = ['xo-cli', action]
    if use_json:
        run_array += ['--json']
    run_array += ["%s=%s" % (key, value) for key, value in args.items()]
    res = subprocess.run(
        run_array,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=check
    )
    if simple_output:
        output = res.stdout.decode().strip()
        if use_json:
            return json.loads(output)
        return output
    return res

def xo_object_exists(uuid):
    lst = json.loads(xo_cli('--list-objects', {'uuid': uuid}))
    return len(lst) > 0
