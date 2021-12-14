import logging
import subprocess

import lib.config as config


class BaseCommandFailed(Exception):
    __slots__ = 'returncode', 'stdout', 'cmd'

    def __init__(self, returncode, stdout, cmd, exception_msg):
        super(BaseCommandFailed, self).__init__(exception_msg)
        self.returncode = returncode
        self.stdout = stdout
        self.cmd = cmd

class SSHCommandFailed(BaseCommandFailed):
    def __init__(self, returncode, stdout, cmd):
        super(SSHCommandFailed, self).__init__(
            returncode, stdout, cmd,
            f'SSH command ({cmd}) failed with return code {returncode}: {stdout}'
        )

class LocalCommandFailed(BaseCommandFailed):
    def __init__(self, returncode, stdout, cmd):
        super(SSHCommandFailed, self).__init__(
            returncode, stdout, cmd,
            f'Local command ({cmd}) failed with return code {returncode}: {stdout}'
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

OUPUT_LOGGER = logging.getLogger('output')
OUPUT_LOGGER.propagate = False
OUTPUT_HANDLER = logging.StreamHandler()
OUPUT_LOGGER.addHandler(OUTPUT_HANDLER)
OUTPUT_HANDLER.setFormatter(logging.Formatter('%(message)s'))

def ssh(hostname_or_ip, cmd, check=True, simple_output=True, suppress_fingerprint_warnings=True,
        background=False, target_os='linux', decode=True):
    options = []
    if suppress_fingerprint_warnings:
        # Suppress warnings and questions related to host key fingerprints
        # because on a test network IPs get reused, VMs are reinstalled, etc.
        # Based on https://unix.stackexchange.com/a/365976/257493
        options.append('-o "StrictHostKeyChecking no"')
        options.append('-o "LogLevel ERROR"')
        options.append('-o "UserKnownHostsFile /dev/null"')

    command = " ".join(cmd)
    if background and target_os != "windows":
        # https://stackoverflow.com/questions/29142/getting-ssh-to-execute-a-command-in-the-background-on-target-machine
        command = "nohup %s &>/dev/null &" % command

    ssh_cmd = "ssh root@%s %s '%s'" % (hostname_or_ip, ' '.join(options), command)

    windows_background = background and target_os == "windows"
    # Fetch banner and remove it to avoid stdout/stderr pollution.
    if config.ignore_ssh_banner and not windows_background:
        banner_res = subprocess.run(
            "ssh root@%s %s '%s'" % (hostname_or_ip, ' '.join(options), '\n'),
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False
        )

    process = subprocess.Popen(
        ssh_cmd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT
    )
    logging.debug(f"[{hostname_or_ip}] {command}")
    if windows_background:
        return process

    stdout = []
    for line in iter(process.stdout.readline, b''):
        readable_line = line.decode(errors='replace').strip()
        if readable_line == '':
            break
        stdout.append(line)
        OUPUT_LOGGER.debug(readable_line)
    _, stderr = process.communicate()
    res = subprocess.CompletedProcess(ssh_cmd, process.returncode, b''.join(stdout), stderr)

    # Get a decoded version of the output in any case, replacing potential errors
    output_for_errors = res.stdout.decode(errors='replace').strip()

    # Even if check is False, we still raise in case of return code 255, which means a SSH error.
    if res.returncode == 255:
        raise SSHCommandFailed(255, "SSH Error: %s" % output_for_errors, command)

    output = res.stdout
    if config.ignore_ssh_banner:
        if banner_res.returncode == 255:
            raise SSHCommandFailed(255, "SSH Error: %s" % banner_res.stdout.decode(errors='replace'), command)
        output = output[len(banner_res.stdout):]

    if decode:
        output = output.decode()

    if res.returncode and check:
        raise SSHCommandFailed(res.returncode, output_for_errors, command)

    if simple_output:
        return output.strip()
    return SSHResult(res.returncode, output)

def scp(hostname_or_ip, src, dest, check=True, suppress_fingerprint_warnings=True, local_dest=False):
    options = ""
    if suppress_fingerprint_warnings:
        # Suppress warnings and questions related to host key fingerprints
        # because on a test network IPs get reused, VMs are reinstalled, etc.
        # Based on https://unix.stackexchange.com/a/365976/257493
        options = '-o "StrictHostKeyChecking no" -o "LogLevel ERROR" -o "UserKnownHostsFile /dev/null"'

    if local_dest:
        src = 'root@{}:{}'.format(hostname_or_ip, src)
    else:
        dest = 'root@{}:{}'.format(hostname_or_ip, dest)

    command = "scp {} {} {}".format(options, src, dest)
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

def local_cmd(cmd, check=True, decode=True):
    """ Run a command locally on tester end. """
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
