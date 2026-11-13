import pytest

import socket
import subprocess
import time

from lib.commands import SSHCommandFailed, ssh
from lib.common import Defer
from lib.host import Host
from lib.netutil import wrap_ip

# Checks that local TCP forwarding (ssh -L) works out of the box, as documented in
# https://docs.xcp-ng.org/vms/#use-a-vnc-client to reach a VM console through the host,
# only towards the host loopback (PermitOpen localhost:* 127.0.0.1:* [::1]:*), and that
# the other kinds of forwarding are refused.
# Requirements:
# - an XCP-ng host (--hosts) >= 8.3

TUNNEL_TIMEOUT = 30

def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]

def _ssh_cmd(host: Host, *args: str) -> list[str]:
    return ['ssh', f'root@{host.hostname_or_ip}',
            '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=no',
            '-o', 'UserKnownHostsFile=/dev/null', '-o', 'LogLevel=ERROR',
            # without multiplexing, as an existing master connection would not carry the forwarding
            '-o', 'ControlMaster=no', '-o', 'ControlPath=none',
            *args]

def _open_local_forward(host: Host, defer: Defer, local_port: int, remote_target: str) -> None:
    # remote_target is either host:port (TCP) or the path of a Unix socket on the host
    cmd = _ssh_cmd(host, '-N', '-o', 'ExitOnForwardFailure=yes',
                   '-L', f'127.0.0.1:{local_port}:{remote_target}')
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def _close() -> None:
        proc.terminate()
        proc.wait(timeout=10)
    defer(_close)

    # wait for the local end of the tunnel to listen
    deadline = time.monotonic() + TUNNEL_TIMEOUT
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            assert proc.stderr is not None
            pytest.fail(f"ssh -L exited with {proc.returncode}: {proc.stderr.read().decode()}")
        try:
            with socket.create_connection(('127.0.0.1', local_port), timeout=1):
                return
        except OSError:
            time.sleep(0.5)
    pytest.fail(f"ssh -L tunnel not listening on 127.0.0.1:{local_port} after {TUNNEL_TIMEOUT}s")

def _assert_tunnel_refused(local_port: int, what: str) -> None:
    # The local end listens anyway, the refusal only happens when the channel is opened:
    # the connection is then closed without any data coming through.
    with socket.create_connection(('127.0.0.1', local_port), timeout=TUNNEL_TIMEOUT) as s:
        s.settimeout(TUNNEL_TIMEOUT)
        s.sendall(b'GET / HTTP/1.0\r\n\r\n')
        try:
            data = s.recv(256)
        except ConnectionResetError:
            data = b''
    assert data == b'', f"{what} should be refused, got data through the tunnel: {data!r}"

@pytest.mark.parametrize("target", ['localhost', '127.0.0.1', '[::1]'])
def test_local_forwarding_to_host_loopback(host: Host, defer: Defer, target: str) -> None:
    # Forward a local port to a TCP service only reached through the host's loopback
    # (here sshd itself), like the documented VNC workflow does with socat's port.
    if target == '[::1]' and '::1' not in host.ssh('ip -6 addr show dev lo'):
        pytest.skip("no IPv6 loopback address on the host")
    local_port = _free_local_port()
    _open_local_forward(host, defer, local_port, f'{target}:22')

    with socket.create_connection(('127.0.0.1', local_port), timeout=TUNNEL_TIMEOUT) as s:
        s.settimeout(TUNNEL_TIMEOUT)
        banner = s.recv(256)
    assert banner.startswith(b'SSH-2.0-OpenSSH_'), f"unexpected data through the tunnel: {banner!r}"

def test_local_forwarding_to_other_address_refused(host: Host, defer: Defer) -> None:
    # sshd listens on the host management address too, but PermitOpen only allows
    # forwarding towards the loopback
    local_port = _free_local_port()
    _open_local_forward(host, defer, local_port, f'{wrap_ip(host.hostname_or_ip)}:22')
    _assert_tunnel_refused(local_port, f"forwarding to {host.hostname_or_ip}:22")

def test_stdio_forwarding_to_other_address_refused(host: Host) -> None:
    # ssh -W, which ssh -J relies on to use the host as a jump host, goes through
    # PermitOpen too: the host can't be used as a gateway to another machine.
    # When allowed, ssh -W would print the sshd banner then exit 0 on stdin EOF.
    target = f'{wrap_ip(host.hostname_or_ip)}:22'
    res = subprocess.run(_ssh_cmd(host, '-W', target), stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=TUNNEL_TIMEOUT)
    assert res.returncode != 0, f"ssh -W {target} should be refused, got: {res.stdout!r}"

def test_remote_forwarding_refused(host: Host) -> None:
    # remote forwardings are requested before the session starts, so the refusal
    # makes ssh exit before running the command
    options = ['-o', 'ExitOnForwardFailure=yes', '-R', '0:localhost:22']
    # without multiplexing, as an existing master connection would ignore the options
    with pytest.raises(SSHCommandFailed):
        ssh(host.hostname_or_ip, 'true', options=options, multiplexing=False)

def test_unix_socket_forwarding_refused(host: Host, defer: Defer) -> None:
    local_port = _free_local_port()
    _open_local_forward(host, defer, local_port, '/var/lib/xcp/xapi')
    _assert_tunnel_refused(local_port, "Unix socket forwarding")
