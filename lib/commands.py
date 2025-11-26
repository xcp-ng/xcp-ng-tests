import base64
import logging
import subprocess

import lib.config as config

from typing import List, Literal, Union, overload

class BaseCommandFailed(Exception):
    __slots__ = 'returncode', 'stdout', 'cmd'

    def __init__(self, returncode, stdout, cmd, exception_msg):
        super(BaseCommandFailed, self).__init__(exception_msg)
        self.returncode = returncode
        self.stdout = stdout
        self.cmd = cmd

class LocalCommandFailed(BaseCommandFailed):
    def __init__(self, returncode, stderr, cmd):
        msg_end = f": {stderr}" if stderr else "."
        super(LocalCommandFailed, self).__init__(
            returncode, stderr, cmd,
            f'Local command ({cmd}) failed with return code {returncode}{msg_end}'
        )

class BaseCmdResult:
    __slots__ = 'returncode', 'stdout'

    def __init__(self, returncode, stdout):
        self.returncode = returncode
        self.stdout = stdout

class LocalCommandResult(BaseCmdResult):
    def __init__(self, returncode, stdout, stderr):
        super(LocalCommandResult, self).__init__(returncode, stdout)
        self.stderr = stderr

def _ellide_log_lines(log):
    if log == '':
        return log

    if config.ssh_output_max_lines < 1:
        return "\n{}".format(log)

    reduced_message = log.split("\n")
    if len(reduced_message) > config.ssh_output_max_lines:
        reduced_message = reduced_message[:config.ssh_output_max_lines - 1]
        reduced_message.append("(...)")
    return "\n{}".format("\n".join(reduced_message))

@overload
def local_cmd(cmd: Union[str, List[str]], *, check: bool = True, simple_output: Literal[True] = True,
              decode: Literal[True] = True) -> str:
    ...
@overload
def local_cmd(cmd: Union[str, List[str]], *, check: bool = True, simple_output: Literal[True] = True,
              decode: Literal[False]) -> bytes:
    ...
@overload
def local_cmd(cmd: Union[str, List[str]], *, check: bool = True, simple_output: Literal[False],
              decode: bool = True) -> LocalCommandResult:
    ...
@overload
def local_cmd(cmd: Union[str, List[str]], *, check: bool = True, simple_output: bool = True,
              decode: bool = True) -> Union[str, bytes, LocalCommandResult]:
    ...

def local_cmd(cmd, check=True, simple_output=True, decode=True):
    """ Run a command locally on tester end. """
    logging.debug("[local] %s", (cmd,))
    res = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False
    )

    # get a decoded version of the output in any case, replacing potential errors
    stdout_for_logs = res.stdout.decode(errors='replace').strip()
    stderr_for_logs = res.stderr.decode(errors='replace').strip()

    errorcode_msg = "" if res.returncode == 0 else " - Got error code: %s" % res.returncode
    command = " ".join(cmd)
    logging.debug(f"[local] {command}{errorcode_msg}{_ellide_log_lines(stdout_for_logs)}")

    if res.returncode and check:
        logging.warning(f"[local] stderr:{_ellide_log_lines(stderr_for_logs)}")
        raise LocalCommandFailed(res.returncode, stderr_for_logs, command)

    output: Union[bytes, str] = res.stdout
    if decode:
        output = output.decode()
    if simple_output:
        return output.strip()

    stderr = res.stderr.decode()
    return LocalCommandResult(res.returncode, output, stderr)

def encode_powershell_command(cmd: str):
    return base64.b64encode(cmd.encode("utf-16-le")).decode("ascii")
