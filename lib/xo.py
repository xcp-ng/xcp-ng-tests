import json
import subprocess

from lib.typing import JSONType

from typing import Dict, Literal, overload

# TODO: either:
#   * replace simple_output and use_json by a single output type
#   * make sure that simple_output=False and use_json=True are not being used together

@overload
def xo_cli(action: str, args: Dict[str, str] = ..., *, check: bool = ..., simple_output: Literal[True] = ...,
           use_json: Literal[False] = ...) -> str:
    ...
@overload
def xo_cli(action: str, args: Dict[str, str] = {}, *, check: bool = True, simple_output: Literal[True] = True,
           use_json: Literal[True]) -> JSONType:
    ...
@overload
def xo_cli(action: str, args: Dict[str, str] = ..., *, check: bool = ..., simple_output: Literal[False],
           use_json: bool = ...) -> subprocess.CompletedProcess[bytes]:
    ...
@overload
def xo_cli(action: str, args: Dict[str, str] = {}, *, check: bool = True, simple_output: bool = True,
           use_json: bool = False) -> subprocess.CompletedProcess[bytes] | JSONType | str:
    ...
def xo_cli(
    action: str, args: dict[str, str] = {}, check: bool = True, simple_output: bool = True, use_json: bool = False
) -> subprocess.CompletedProcess[bytes] | JSONType | str:
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

def xo_object_exists(uuid: str) -> bool:
    lst = json.loads(xo_cli('--list-objects', {'uuid': uuid}))
    return len(lst) > 0
