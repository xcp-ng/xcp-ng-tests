from __future__ import annotations

import pytest

import logging
import shlex
import time

from lib.blktap import TapCtl
from lib.common import MiB, wait_for
from tests.storage.blktap import wait_for_tapdisk_exit

from typing import TYPE_CHECKING, Generator

if TYPE_CHECKING:
    from lib.common import Defer
    from lib.host import Host

# The NBD server of tapdisk, through its unix socket in dom0
# (/var/run/blktap-control/nbd<pid>.<minor>), with a small NBD client run in dom0.
#
# Requirements:
# - an XCP-ng host with blktap and python3 in dom0

# NBD client, run in dom0 on the NBD socket of a tapdisk:
# - "overlapping": two handshakes at the same time, the first client leaves
#   before sending its flags, then the second one;
# - "options": a single client sends its flags, then leaves during the option
#   negotiation;
# - "read": a full handshake (NBD_OPT_EXPORT_NAME), then a read of the first
#   sector; prints the export size;
# - "reads": a full handshake, then 1 MiB reads all over the export, always
#   READS_IN_FLIGHT of them in flight, until the server closes the connection;
#   prints the number of reads completed.
NBD_CLIENT = r'''
import socket, struct, sys

path, scenario = sys.argv[1], sys.argv[2]
READS_IN_FLIGHT = 8
READ_SIZE = 1024 * 1024

def recv(s, size):
    data = b''
    while len(data) < size:
        chunk = s.recv(size - len(data))
        if not chunk:
            raise EOFError(f"connection closed by the server after {len(data)}/{size} bytes")
        data += chunk
    return data

def connect():
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(10)
    s.connect(path)
    greeting = recv(s, 18)
    assert greeting[:16] == b'NBDMAGICIHAVEOPT', f"unexpected greeting {greeting!r}"
    return s

def handshake():
    s = connect()
    s.sendall(struct.pack('>I', 1))  # client flags: fixed newstyle
    # IHAVEOPT, NBD_OPT_EXPORT_NAME, no name
    s.sendall(struct.pack('>QII', 0x49484156454F5054, 1, 0))
    size, _ = struct.unpack('>QH', recv(s, 10))
    recv(s, 124)  # zeroes, as the client didn't send NBD_FLAG_C_NO_ZEROES
    return s, size

def send_read(s, handle, offset, length):
    s.sendall(struct.pack('>IHHQQI', 0x25609513, 0, 0, handle, offset, length))  # NBD_CMD_READ

def recv_read(s, length):
    magic, error, handle = struct.unpack('>IIQ', recv(s, 16))
    assert (magic, error) == (0x67446698, 0), f"unexpected reply {(magic, error, handle)}"
    recv(s, length)
    return handle

if scenario == 'overlapping':
    first = connect()
    second = connect()
    first.close()
    second.close()
elif scenario == 'options':
    s = connect()
    s.sendall(struct.pack('>I', 1))
    s.close()
elif scenario == 'read':
    s, size = handshake()
    send_read(s, 42, 0, 512)
    assert recv_read(s, 512) == 42, "reply to another request"
    s.close()
    print(size)
elif scenario == 'reads':
    s, size = handshake()
    blocks = size // READ_SIZE
    for handle in range(READS_IN_FLIGHT):
        send_read(s, handle, handle % blocks * READ_SIZE, READ_SIZE)
    completed = 0
    try:
        while True:
            recv_read(s, READ_SIZE)
            completed += 1
            handle = completed + READS_IN_FLIGHT - 1
            send_read(s, handle, handle % blocks * READ_SIZE, READ_SIZE)
    except (EOFError, OSError):
        pass
    print(completed, flush=True)
'''

# a spinning tapdisk uses a whole CPU, an idle one almost nothing
MAX_IDLE_CPU_RATIO = 0.2
CPU_SAMPLE_SECS = 5

def nbd_client(host: Host, socket_path: str, scenario: str) -> str:
    return host.ssh(shlex.join(['python3', '-c', NBD_CLIENT, socket_path, scenario]))

def cpu_secs(host: Host, pid: int) -> float:
    # utime and stime, fields 14 and 15 of /proc/<pid>/stat, in clock ticks
    ticks = host.ssh(f"awk '{{print $14 + $15}}' /proc/{pid}/stat")
    return int(ticks) / int(host.ssh('getconf CLK_TCK'))

@pytest.fixture
def raw_image_path(host: Host) -> Generator[str, None, None]:
    """A 64 MiB raw image in dom0 /tmp, allocated so that the reads go to the disk."""
    path = host.ssh('mktemp /tmp/blktap-test-XXXXXX.raw')
    host.ssh(f'dd if=/dev/zero of={path} bs=1M count=64 status=none')
    yield path
    host.ssh(f'rm -f {path}')

@pytest.mark.no_vm
@pytest.mark.parametrize('scenario', ['overlapping', 'options'])
def test_nbd_client_leaving_during_handshake(
    host: Host, vhd_path: str, tapctl: TapCtl, defer: Defer, scenario: str
) -> None:
    """The tapdisk stays idle and serves NBD clients after one left during the handshake."""
    # blktap commit "tapdisk: nbdserver: fix the handshake error paths spinning on EBADF":
    #   the handshake callback used to work on a fd shared by the whole server, overwritten by
    #   every new client, and the option negotiation error path enabled the client on the fd it
    #   had just closed. In both cases a closed fd stayed registered in the scheduler, which then
    #   spun on select() failing with EBADF, using a whole CPU and logging ~120k lines per second.
    pid, minor = tapctl.create(f"vhd:{vhd_path}")
    socket_path = f'/var/run/blktap-control/nbd{pid}.{minor}'
    log_path = f'/var/log/blktap/tapdisk.{pid}.log'
    # a spinning tapdisk would fill /var/log within minutes: kill it without
    # waiting for the teardown of tapctl, which would go through its control
    # socket, then remove its log
    defer(lambda: host.ssh(f'rm -f {log_path}'))
    defer(lambda: host.ssh(f'kill -9 {pid}', check=False))

    logging.info(f"NBD clients leaving during the handshake ({scenario}) on {socket_path}")
    nbd_client(host, socket_path, scenario)

    # nothing to wait for: the CPU usage of the tapdisk is measured over a fixed time
    start = cpu_secs(host, pid)
    time.sleep(CPU_SAMPLE_SECS)
    cpu_ratio = (cpu_secs(host, pid) - start) / CPU_SAMPLE_SECS
    ebadf_lines = int(host.ssh(f'grep -c "Bad file descriptor" {log_path} || true'))
    assert ebadf_lines == 0, \
        f"tapdisk {pid} logged {ebadf_lines} EBADF lines: it spins on a closed fd"
    assert cpu_ratio < MAX_IDLE_CPU_RATIO, \
        f"tapdisk {pid} used {cpu_ratio:.0%} of a CPU while idle"

    size = nbd_client(host, socket_path, 'read')
    assert int(size) == 100 * MiB, \
        f"the NBD server of tapdisk {pid} exports {size} bytes, expected 100 MiB"

    # the control socket answers: the tapdisk releases its VBD and exits
    tapctl.close(pid, minor, timeout=30)
    tapctl.detach(pid, minor)
    wait_for_tapdisk_exit(host, pid)
    tapctl.free(minor)

@pytest.mark.no_vm
def test_close_with_nbd_reads_in_flight(
    host: Host, raw_image_path: str, tapctl: TapCtl, defer: Defer
) -> None:
    """Closing the image with NBD reads in flight disconnects the client, tapdisk survives."""
    # blktap commit "tapdisk: wait for the NBD requests in flight before closing an image":
    #   tap-ctl close freed the NBD servers while their clients still had requests in flight.
    #   When such a request completed, its callback used the freed server, and tapdisk crashed:
    #   the VBD could then not be unplugged anymore.
    pid, minor = tapctl.create(f"aio:{raw_image_path}")
    socket_path = f'/var/run/blktap-control/nbd{pid}.{minor}'
    output_path = host.ssh('mktemp /tmp/blktap-test-XXXXXX.out')
    defer(lambda: host.ssh(f'rm -f {output_path}'))

    logging.info(f"Keep NBD reads in flight on {socket_path}")
    client = shlex.join(['python3', '-c', NBD_CLIENT, socket_path, 'reads'])
    client_pid = host.ssh(f'setsid {client} > {output_path} 2>&1 < /dev/null & echo $!')
    defer(lambda: host.ssh(f'kill {client_pid}', check=False))
    # the whole image read once: the reads are going on
    wait_for(lambda: tapctl.stats(pid, minor)['secs'][0] >= 64 * MiB // 512,
             "Wait for the NBD reads to reach tapdisk", timeout_secs=30)

    logging.info(f"Close the image of tapdisk {pid} with NBD reads in flight")
    tapctl.close(pid, minor, timeout=30)
    wait_for(lambda: host.ssh_with_result(f'test -d /proc/{client_pid}').returncode != 0,
             "Wait for the NBD client to be disconnected", timeout_secs=30)
    output = host.ssh(f'cat {output_path}')
    assert output.isdigit(), \
        f"the NBD client failed:\n{output}"

    assert host.ssh_with_result(f'test -d /proc/{pid}').returncode == 0, \
        f"tapdisk {pid} died after closing its image with NBD reads in flight"
    tapctl.detach(pid, minor)
    wait_for_tapdisk_exit(host, pid)
    tapctl.free(minor)
