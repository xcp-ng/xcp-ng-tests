import base64
import logging
import shlex
import subprocess
from typing import List, Literal, Union, overload

import lib.config as config
from lib.netutil import wrap_ip

class BaseCommandFailed(Exception):
    __slots__ = 'returncode', 'stdout', 'cmd'

    def __init__(self, returncode, stdout, cmd, exception_msg):
        super(BaseCommandFailed, self).__init__(exception_msg)
        self.returncode = returncode
        self.stdout = stdout
        self.cmd = cmd

class SSHCommandFailed(BaseCommandFailed):
    def __init__(self, returncode, stdout, cmd):
        msg_end = f": {stdout}" if stdout else "."
        super(SSHCommandFailed, self).__init__(
            returncode, stdout, cmd,
            f'SSH command ({cmd}) failed with return code {returncode}{msg_end}'
        )

class LocalCommandFailed(BaseCommandFailed):
    def __init__(self, returncode, stdout, cmd):
        msg_end = f": {stdout}" if stdout else "."
        super(LocalCommandFailed, self).__init__(
            returncode, stdout, cmd,
            f'Local command ({cmd}) failed with return code {returncode}{msg_end}'
        )

class BaseCmdResult:
    __slots__ = 'returncode', 'stdout'

    def __init__(self, returncode, stdout):
        self.returncode = returncode
        self.stdout = stdout

class SSHResult(BaseCmdResult):
    def __init__(self, returncode, stdout):
        super(SSHResult, self).__init__(returncode, stdout)

class LocalCommandResult(BaseCmdResult):
    def __init__(self, returncode, stdout):
        super(LocalCommandResult, self).__init__(returncode, stdout)

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

def _ssh(hostname_or_ip, cmd, check, simple_output, suppress_fingerprint_warnings,
         background, decode, options) -> Union[SSHResult, SSHCommandFailed, str, bytes, None]:
    opts = list(options)
    opts.append('-o "BatchMode yes"')
    if suppress_fingerprint_warnings:
        # Suppress warnings and questions related to host key fingerprints
        # because on a test network IPs get reused, VMs are reinstalled, etc.
        # Based on https://unix.stackexchange.com/a/365976/257493
        opts.append('-o "StrictHostKeyChecking no"')
        opts.append('-o "LogLevel ERROR"')
        opts.append('-o "UserKnownHostsFile /dev/null"')

    if isinstance(cmd, str):
        command = cmd
    else:
        command = " ".join(cmd)

    ssh_cmd = f"ssh root@{hostname_or_ip} {' '.join(opts)} {shlex.quote(command)}"

    # Fetch banner and remove it to avoid stdout/stderr pollution.
    banner_res = None
    if config.ignore_ssh_banner:
        banner_res = subprocess.run(
            "ssh root@%s %s '%s'" % (hostname_or_ip, ' '.join(opts), '\n'),
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False
        )

    logging.debug(f"[{hostname_or_ip}] {command}")
    process = subprocess.Popen(
        ssh_cmd,
        shell=True,
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
        return SSHCommandFailed(255, "SSH Error: %s" % output_for_errors, command)

    output: Union[bytes, str] = res.stdout
    if banner_res:
        if banner_res.returncode == 255:
            return SSHCommandFailed(255, "SSH Error: %s" % banner_res.stdout.decode(errors='replace'), command)
        output = output[len(banner_res.stdout):]

    if decode:
        assert isinstance(output, bytes)
        output = output.decode()

    if res.returncode and check:
        return SSHCommandFailed(res.returncode, output_for_errors, command)

    if simple_output:
        return output.strip()
    return SSHResult(res.returncode, output)

# The actual code is in _ssh().
# This function is kept short for shorter pytest traces upon SSH failures, which are common,
# as pytest prints the whole function definition that raised the SSHCommandFailed exception
@overload
def ssh(hostname_or_ip: str, cmd: Union[str, List[str]], *, check: bool = True, simple_output: Literal[True] = True,
        suppress_fingerprint_warnings: bool = True, background: Literal[False] = False,
        decode: Literal[True] = True, options: List[str] = []) -> str:
    ...
@overload
def ssh(hostname_or_ip: str, cmd: Union[str, List[str]], *, check: bool = True, simple_output: Literal[True] = True,
        suppress_fingerprint_warnings: bool = True, background: Literal[False] = False,
        decode: Literal[False], options: List[str] = []) -> bytes:
    ...
@overload
def ssh(hostname_or_ip: str, cmd: Union[str, List[str]], *, check: bool = True, simple_output: Literal[False],
        suppress_fingerprint_warnings: bool = True, background: Literal[False] = False,
        decode: bool = True, options: List[str] = []) -> SSHResult:
    ...
@overload
def ssh(hostname_or_ip: str, cmd: Union[str, List[str]], *, check: bool = True, simple_output: Literal[False],
        suppress_fingerprint_warnings: bool = True, background: Literal[True],
        decode: bool = True, options: List[str] = []) -> None:
    ...
@overload
def ssh(hostname_or_ip: str, cmd: Union[str, List[str]], *, check=True, simple_output: bool = True,
        suppress_fingerprint_warnings=True, background: bool = False,
        decode: bool = True, options: List[str] = []) \
        -> Union[str, bytes, SSHResult, None]:
    ...
def ssh(hostname_or_ip, cmd, *, check=True, simple_output=True,
        suppress_fingerprint_warnings=True,
        background=False, decode=True, options=[]):
    result_or_exc = _ssh(hostname_or_ip, cmd, check, simple_output, suppress_fingerprint_warnings,
                         background, decode, options)
    if isinstance(result_or_exc, SSHCommandFailed):
        raise result_or_exc
    else:
        return result_or_exc

def ssh_with_result(hostname_or_ip, cmd, suppress_fingerprint_warnings=True,
                    background=False, decode=True, options=[]) -> SSHResult:
    result_or_exc = _ssh(hostname_or_ip, cmd, False, False, suppress_fingerprint_warnings,
                         background, decode, options)
    if isinstance(result_or_exc, SSHCommandFailed):
        raise result_or_exc
    elif isinstance(result_or_exc, SSHResult):
        return result_or_exc
    assert False, "unexpected type"

def scp(hostname_or_ip, src, dest, check=True, suppress_fingerprint_warnings=True, local_dest=False):
    opts = '-o "BatchMode yes"'
    if suppress_fingerprint_warnings:
        # Suppress warnings and questions related to host key fingerprints
        # because on a test network IPs get reused, VMs are reinstalled, etc.
        # Based on https://unix.stackexchange.com/a/365976/257493
        opts = '-o "StrictHostKeyChecking no" -o "LogLevel ERROR" -o "UserKnownHostsFile /dev/null"'

    ip = wrap_ip(hostname_or_ip)
    if local_dest:
        src = 'root@{}:{}'.format(ip, src)
    else:
        dest = 'root@{}:{}'.format(ip, dest)

    command = "scp {} {} {}".format(opts, src, dest)
    res = subprocess.run(
        command,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False
    )

    errorcode_msg = "" if res.returncode == 0 else " - Got error code: %s" % res.returncode
    logging.debug(f"[{hostname_or_ip}] scp: {src} => {dest}{errorcode_msg}")

    if check and res.returncode:
        raise SSHCommandFailed(res.returncode, res.stdout.decode(), command)

    return res

def sftp(hostname_or_ip, cmds, check=True, suppress_fingerprint_warnings=True):
    opts = ''
    if suppress_fingerprint_warnings:
        # Suppress warnings and questions related to host key fingerprints
        # because on a test network IPs get reused, VMs are reinstalled, etc.
        # Based on https://unix.stackexchange.com/a/365976/257493
        opts = '-o "StrictHostKeyChecking no" -o "LogLevel ERROR" -o "UserKnownHostsFile /dev/null"'

    args = "sftp {} -b - root@{}".format(opts, hostname_or_ip)
    input = bytes("\n".join(cmds), 'utf-8')
    res = subprocess.run(
        args,
        input=input,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False
    )

    if check and res.returncode:
        raise SSHCommandFailed(res.returncode, res.stdout.decode(), "{} -- {}".format(args, cmds))

    return res

def local_cmd(cmd, check=True, decode=True):
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

    output = res.stdout
    if decode:
        output = output.decode()

    errorcode_msg = "" if res.returncode == 0 else " - Got error code: %s" % res.returncode
    command = " ".join(cmd)
    logging.debug(f"[local] {command}{errorcode_msg}{_ellide_log_lines(output_for_logs)}")

    if res.returncode and check:
        raise LocalCommandFailed(res.returncode, output_for_logs, command)

    return LocalCommandResult(res.returncode, output)

def encode_powershell_command(cmd: str):
    return base64.b64encode(cmd.encode("utf-16-le")).decode("ascii")
