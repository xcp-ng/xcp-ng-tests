from __future__ import annotations

import base64
import logging
import os
import platform
import subprocess

import lib.config as config
from lib.netutil import wrap_ip

from typing import TYPE_CHECKING, Generic, List, Literal, TypeVar, overload

if TYPE_CHECKING:
    from lib.common import HostAddress

class BaseCommandFailed(Exception):
    __slots__ = 'returncode', 'stdout', 'cmd'

    def __init__(self, returncode: int, stdout: str, cmd: str | list[str], exception_msg: str):
        super(BaseCommandFailed, self).__init__(exception_msg)
        self.returncode = returncode
        self.stdout = stdout
        self.cmd = cmd

class SSHCommandFailed(BaseCommandFailed):
    def __init__(self, returncode: int, stdout: str, cmd: str):
        msg_end = f": {stdout}" if stdout else "."
        super(SSHCommandFailed, self).__init__(
            returncode, stdout, cmd,
            f'SSH command ({cmd}) failed with return code {returncode}{msg_end}'
        )

class LocalCommandFailed(BaseCommandFailed):
    def __init__(self, returncode: int, stdout: str, cmd: str | list[str]):
        msg_end = f": {stdout}" if stdout else "."
        super(LocalCommandFailed, self).__init__(
            returncode, stdout, cmd,
            f'Local command ({cmd}) failed with return code {returncode}{msg_end}'
        )

ResultOutputT = TypeVar('ResultOutputT', str, bytes)

class BaseCmdResult(Generic[ResultOutputT]):
    __slots__ = 'returncode', 'stdout'

    def __init__(self, returncode: int, stdout: ResultOutputT):
        self.returncode = returncode
        self.stdout: ResultOutputT = stdout

class SSHResult(BaseCmdResult[ResultOutputT]):
    def __init__(self, returncode: int, stdout: ResultOutputT):
        super(SSHResult, self).__init__(returncode, stdout)

class LocalCommandResult(BaseCmdResult[ResultOutputT]):
    def __init__(self, returncode: int, stdout: ResultOutputT):
        super(LocalCommandResult, self).__init__(returncode, stdout)

def _ellide_log_lines(log: str) -> str:
    if log == '':
        return log

    if config.ssh_output_max_lines < 1:
        return "\n{}".format(log)

    reduced_message = log.split("\n")
    if len(reduced_message) > config.ssh_output_max_lines:
        reduced_message = reduced_message[:config.ssh_output_max_lines - 1]
        reduced_message.append("(...)")
    return "\n{}".format("\n".join(reduced_message))

def _ssh(
    hostname_or_ip: str,
    cmd: str,
    check: bool,
    simple_output: bool,
    suppress_fingerprint_warnings: bool,
    background: bool,
    decode: bool,
    options: list[str],
    multiplexing: bool,
) -> SSHResult[str] | SSHResult[bytes] | SSHCommandFailed | str | bytes | None:
    opts = list(options)
    opts += ['-o', 'BatchMode yes']
    opts += ['-o', 'PubkeyAcceptedAlgorithms +ssh-rsa']
    if suppress_fingerprint_warnings:
        # Suppress warnings and questions related to host key fingerprints
        # because on a test network IPs get reused, VMs are reinstalled, etc.
        # Based on https://unix.stackexchange.com/a/365976/257493
        opts += ['-o', 'StrictHostKeyChecking no']
        opts += ['-o', 'LogLevel ERROR']
        opts += ['-o', 'UserKnownHostsFile /dev/null']
    # It could work with git bash—we might want to check that instead.
    # We use the pid in the control path to avoid a race condition on the master socket creation
    # when running the tests in parallel. The socket is removed by the ssh client olding the master
    # connection when it reaches the timeout.
    if multiplexing and platform.system() != "Windows":
        opts += ['-o', 'ControlMaster auto']
        opts += ['-o', f'ControlPath ~/.ssh/control-{os.getpid()}:%h:%p:%r']
        opts += ['-o', 'ControlPersist 10m']
        opts += ['-o', 'ServerAliveInterval 10s']
    else:
        opts += ['-o', 'ControlMaster no']

    ssh_cmd = ['ssh', f'root@{hostname_or_ip}'] + opts + [cmd]

    # Fetch banner and remove it to avoid stdout/stderr pollution.
    banner_res = None
    if config.ignore_ssh_banner:
        banner_res = subprocess.run(
            ['ssh', f'root@{hostname_or_ip}'] + opts + ['\n'],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False
        )

    logging.debug(f"[{hostname_or_ip}] {cmd}")
    process = subprocess.Popen(
        ssh_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT
    )
    if background:
        return None

    stdout = []
    assert process.stdout is not None
    for line in iter(process.stdout.readline, b''):
        readable_line = line.decode(errors='replace').strip()
        stdout.append(line)
        logging.debug("> %s", readable_line)
    _, stderr = process.communicate()
    res = subprocess.CompletedProcess(ssh_cmd, process.returncode, b''.join(stdout), stderr)

    # Get a decoded version of the output in any case, replacing potential errors
    output_for_errors = res.stdout.decode(errors='replace').strip()

    # Even if check is False, we still raise in case of return code 255, which means a SSH error.
    if res.returncode == 255:
        return SSHCommandFailed(255, "SSH Error: %s" % output_for_errors, cmd)

    output: bytes = res.stdout
    if banner_res:
        if banner_res.returncode == 255:
            return SSHCommandFailed(255, "SSH Error: %s" % banner_res.stdout.decode(errors='replace'), cmd)
        output = output[len(banner_res.stdout):]

    if res.returncode and check:
        return SSHCommandFailed(res.returncode, output_for_errors, cmd)

    if decode:
        output_str = output.decode()
        if simple_output:
            return output_str.strip()
        else:
            return SSHResult[str](res.returncode, output_str)
    else:
        if simple_output:
            return output.strip()
        else:
            return SSHResult[bytes](res.returncode, output)

# The actual code is in _ssh().
# This function is kept short for shorter pytest traces upon SSH failures, which are common,
# as pytest prints the whole function definition that raised the SSHCommandFailed exception
@overload
def ssh(hostname_or_ip: HostAddress, cmd: str, *, check: bool = ...,
        simple_output: Literal[True] = ...,
        suppress_fingerprint_warnings: bool = ..., background: Literal[False] = ...,
        decode: Literal[True] = ..., options: List[str] = ..., multiplexing: bool = ...) -> str:
    ...
@overload
def ssh(hostname_or_ip: HostAddress, cmd: str, *, check: bool = ...,
        simple_output: Literal[True] = ...,
        suppress_fingerprint_warnings: bool = ..., background: Literal[False] = ...,
        decode: Literal[False], options: List[str] = ..., multiplexing: bool = ...) -> bytes:
    ...
@overload
def ssh(hostname_or_ip: HostAddress, cmd: str, *, check: bool = ...,
        simple_output: Literal[False],
        suppress_fingerprint_warnings: bool = ..., background: Literal[False] = ...,
        decode: Literal[True] = ..., options: List[str] = ..., multiplexing: bool = ...) -> SSHResult[str]:
    ...
@overload
def ssh(hostname_or_ip: HostAddress, cmd: str, *, check: bool = ...,
        simple_output: Literal[False],
        suppress_fingerprint_warnings: bool = ..., background: Literal[False] = ...,
        decode: Literal[False], options: List[str] = ..., multiplexing: bool = ...) -> SSHResult[bytes]:
    ...
@overload
def ssh(hostname_or_ip: HostAddress, cmd: str, *, check: bool = ...,
        simple_output: Literal[False],
        suppress_fingerprint_warnings: bool = ..., background: Literal[True],
        decode: bool = ..., options: List[str] = ..., multiplexing: bool = ...) -> None:
    ...
@overload
def ssh(hostname_or_ip: HostAddress, cmd: str, *, check: bool = ...,
        simple_output: bool = ...,
        suppress_fingerprint_warnings: bool = ..., background: bool = ...,
        decode: bool = ..., options: List[str] = ..., multiplexing: bool = ...) \
        -> str | bytes | SSHResult[str] | SSHResult[bytes] | None:
    ...
def ssh(hostname_or_ip: HostAddress, cmd: str, *, check: bool = True, simple_output: bool = True,
        suppress_fingerprint_warnings: bool = True,
        background: bool = False, decode: bool = True, options: List[str] = [], multiplexing: bool = True) \
        -> str | bytes | SSHResult[str] | SSHResult[bytes] | None:
    result_or_exc = _ssh(hostname_or_ip, cmd, check, simple_output, suppress_fingerprint_warnings,
                         background, decode, options, multiplexing)
    if isinstance(result_or_exc, SSHCommandFailed):
        raise result_or_exc
    else:
        return result_or_exc

@overload
def ssh_with_result(hostname_or_ip: HostAddress, cmd: str, *, decode: Literal[True] = ...,
                    suppress_fingerprint_warnings: bool = ...,
                    background: bool = ..., options: List[str] = ...,
                    multiplexing: bool = ...) -> SSHResult[str]:
    ...
@overload
def ssh_with_result(hostname_or_ip: HostAddress, cmd: str, *, decode: Literal[False],
                    suppress_fingerprint_warnings: bool = ...,
                    background: bool = ..., options: List[str] = ...,
                    multiplexing: bool = ...) -> SSHResult[bytes]:
    ...
def ssh_with_result(hostname_or_ip: HostAddress, cmd: str, *, suppress_fingerprint_warnings: bool = True,
                    background: bool = False, decode: bool = True, options: List[str] = [],
                    multiplexing: bool = True) -> SSHResult[str] | SSHResult[bytes]:
    result_or_exc = _ssh(hostname_or_ip, cmd, False, False, suppress_fingerprint_warnings,
                         background, decode, options, multiplexing)
    if isinstance(result_or_exc, SSHCommandFailed):
        raise result_or_exc
    elif isinstance(result_or_exc, SSHResult):
        return result_or_exc
    assert False, "unexpected type"

def scp(hostname_or_ip: HostAddress, src: str, dest: str, check: bool = True,
        suppress_fingerprint_warnings: bool = True, local_dest: bool = False) -> subprocess.CompletedProcess[bytes]:
    opts = ['-o', 'BatchMode=yes']
    if suppress_fingerprint_warnings:
        # Suppress warnings and questions related to host key fingerprints
        # because on a test network IPs get reused, VMs are reinstalled, etc.
        # Based on https://unix.stackexchange.com/a/365976/257493
        opts = ['-o', 'StrictHostKeyChecking=no', '-o', 'LogLevel=ERROR', '-o', 'UserKnownHostsFile=/dev/null']

    ip = wrap_ip(hostname_or_ip)
    if local_dest:
        src = 'root@{}:{}'.format(ip, src)
    else:
        dest = 'root@{}:{}'.format(ip, dest)

    command = ['scp'] + opts + [src, dest]
    res = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False
    )

    errorcode_msg = "" if res.returncode == 0 else " - Got error code: %s" % res.returncode
    logging.debug(f"[{hostname_or_ip}] scp: {src} => {dest}{errorcode_msg}")

    if check and res.returncode:
        raise SSHCommandFailed(res.returncode, res.stdout.decode(), ' '.join(command))

    return res

def sftp(
    hostname_or_ip: HostAddress, cmds: List[str], check: bool = True, suppress_fingerprint_warnings: bool = True
) -> subprocess.CompletedProcess[bytes]:
    opts = ''
    if suppress_fingerprint_warnings:
        # Suppress warnings and questions related to host key fingerprints
        # because on a test network IPs get reused, VMs are reinstalled, etc.
        # Based on https://unix.stackexchange.com/a/365976/257493
        opts = '-o "StrictHostKeyChecking no" -o "LogLevel ERROR" -o "UserKnownHostsFile /dev/null"'

    args = "sftp {} -b - root@{}".format(opts, hostname_or_ip)
    input_bytes = bytes("\n".join(cmds), 'utf-8')
    res = subprocess.run(
        args,
        input=input_bytes,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False
    )

    if check and res.returncode:
        raise SSHCommandFailed(res.returncode, res.stdout.decode(), "{} -- {}".format(args, cmds))

    return res

@overload
def local_cmd(cmd: List[str], *, check: bool = ..., decode: Literal[True] = ...) -> LocalCommandResult[str]:
    ...
@overload
def local_cmd(cmd: List[str], *, check: bool = ..., decode: Literal[False]) -> LocalCommandResult[bytes]:
    ...
def local_cmd(
    cmd: List[str], *, check: bool = True, decode: bool = True
) -> LocalCommandResult[str] | LocalCommandResult[bytes]:
    """ Run a command locally on tester end. """
    logging.debug("[local] %s", (cmd,))
    res = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False
    )

    # get a decoded version of the output in any case, replacing potential errors
    output_for_logs = res.stdout.decode(errors='replace').strip()

    errorcode_msg = "" if res.returncode == 0 else " - Got error code: %s" % res.returncode
    command = " ".join(cmd)
    logging.debug(f"[local] {command}{errorcode_msg}{_ellide_log_lines(output_for_logs)}")

    if res.returncode and check:
        raise LocalCommandFailed(res.returncode, output_for_logs, command)

    if decode:
        return LocalCommandResult[str](res.returncode, res.stdout.decode())
    else:
        return LocalCommandResult[bytes](res.returncode, res.stdout)

def encode_powershell_command(cmd: str) -> str:
    return base64.b64encode(cmd.encode("utf-16-le")).decode("ascii")
