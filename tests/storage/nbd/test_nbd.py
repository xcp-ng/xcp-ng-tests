from __future__ import annotations

import pytest

import contextlib
import itertools
import logging
import re
import threading
import time

from lib.common import MiB, wait_for
from lib.host import Host
from lib.nbd import NbdClient, NbdInfo, XapiNbdSession
from lib.network import Network
from lib.vdi import VDI

# Requirements:
# - an XCP-ng host whose main SR can hold two 256 MiB VDIs
# - the test runner must reach the management IP of the host on TCP port 10809: xapi-nbd
#   only accepts the connections arriving on a network with the nbd purpose, which the
#   management network gets for the duration of the tests
#
# These tests reproduce a deadlock between tapdisk and xapi-nbd, seen with XO backups
# overlapping on a host. tapdisk sent each NBD reply with a blocking send() from its event
# loop, so a peer not reading its replies froze the whole tapdisk, `tap-ctl list` included.
# xapi-nbd runs `tap-ctl list` synchronously when a client connects, and doesn't read its
# other connections meanwhile: the tapdisk sending them a reply never answers.
#
# Through xapi-nbd, the deadlock is a race: tapdisk must be in the middle of a reply when
# xapi-nbd stops reading it, which only happens for a few percent of the new connections.
# xapi-nbd reads a whole reply from tapdisk before writing it to its client, so a client
# not reading its replies doesn't block tapdisk: it only blocks xapi-nbd.

# What XO reads per request, much larger than a socket buffer
BLOCK_SIZE = 2 * MiB

class NbdStreamer(threading.Thread):
    """ Reads an NBD export again and again, keeping `depth` reads in flight, like a backup. """

    def __init__(self, client: NbdClient, depth: int = 8):
        super().__init__(daemon=True)
        self.client = client
        self.depth = depth
        self.bytes_read = 0
        self.error: BaseException | None = None
        self._stop_event = threading.Event()

    def run(self) -> None:
        offsets = itertools.cycle(range(0, self.client.size, BLOCK_SIZE))
        try:
            in_flight = 0
            while not self._stop_event.is_set():
                while in_flight < self.depth:
                    offset = next(offsets)
                    self.client.send_read(offset, min(BLOCK_SIZE, self.client.size - offset))
                    in_flight += 1
                _, data = self.client.recv_read()
                in_flight -= 1
                self.bytes_read += len(data)
        except BaseException as e:
            if not self._stop_event.is_set():
                self.error = e

    def stop(self) -> None:
        self._stop_event.set()
        self.client.abort()
        self.join(timeout=60)

def tap_ctl_list_answers(host: Host) -> bool:
    return host.ssh_with_result('timeout 10 tap-ctl list').returncode == 0

def management_nbd_info(host: Host, session: XapiNbdSession, vdi: VDI) -> NbdInfo:
    """ The VDI.get_nbd_info entry on the management network: the one the test runner can reach. """
    address = host.management_pif().param_get('IP')
    infos = session.nbd_info(vdi)
    for info in infos:
        if info.address == address:
            return info
    raise AssertionError(f"no VDI.get_nbd_info entry on {address} for {vdi}: {infos}")

# Connections reading the same VDI at once, like the parallel NBD connections of an XO backup:
# the more of them, the more often tapdisk is in the middle of a reply when xapi-nbd stops
# reading it to serve a new connection
READING_CONNECTIONS = 4
# How long new connections are attempted while the others are reading, in seconds: each one
# only deadlocks if tapdisk is in the middle of a reply at that moment
CONNECTION_DURATION = 5 * 60

@pytest.mark.usefixtures("nbd_network", "tapdisk_control_check")
def test_xapi_nbd_connection_while_reading(host: Host, nbd_session: XapiNbdSession, nbd_vdis: list[VDI]) -> None:
    """ A connection to xapi-nbd must succeed while other connections are reading. """
    reading_vdi, other_vdi = nbd_vdis

    with contextlib.ExitStack() as stack:
        streamers = []
        for _ in range(READING_CONNECTIONS):
            client = stack.enter_context(
                NbdClient.from_nbd_info(management_nbd_info(host, nbd_session, reading_vdi))
            )
            streamer = NbdStreamer(client)
            streamer.start()
            # Registered after the client: stopped before the client is closed
            stack.callback(streamer.stop)
            streamers.append(streamer)

        def errors() -> list[BaseException]:
            return [s.error for s in streamers if s.error is not None]

        wait_for(lambda: all(s.bytes_read >= 64 * MiB for s in streamers) or errors(),
                 "Wait for the NBD connections to be reading", timeout_secs=60, retry_delay_secs=1)
        assert not errors(), f"reading NBD connections failed: {errors()!r}"

        deadline = time.monotonic() + CONNECTION_DURATION
        i = 0
        while time.monotonic() < deadline:
            info = management_nbd_info(host, nbd_session, other_vdi)
            start = time.monotonic()
            timed_out = False
            try:
                with NbdClient.from_nbd_info(info, timeout=30) as client:
                    client.read(0, BLOCK_SIZE)
            except TimeoutError:
                timed_out = True
            if timed_out:
                pytest.fail(
                    f"NBD connection {i} got no answer from xapi-nbd in 30 s while other connections were"
                    f" reading, after {i} successful connections"
                    f" (tap-ctl list answers: {tap_ctl_list_answers(host)})"
                )
            logging.info("NBD connection %d: connected and read %d bytes in %.2f s",
                         i, BLOCK_SIZE, time.monotonic() - start)
            i += 1
        logging.info("%d NBD connections succeeded in %d s while other connections were reading",
                     i, CONNECTION_DURATION)

        read_before = [s.bytes_read for s in streamers]
        time.sleep(2)
        assert not errors(), f"reading NBD connections failed: {errors()!r}"
        assert all(s.bytes_read > before for s, before in zip(streamers, read_before)), \
            "a reading NBD connection stopped reading"
        assert tap_ctl_list_answers(host), "tap-ctl list hangs"

# Reads sent and never read by the stalled connection: far more than the socket buffers
# between tapdisk and the test runner
STALLED_READS = 32

@pytest.mark.usefixtures("nbd_network", "tapdisk_control_check")
def test_xapi_nbd_connection_while_stalled(host: Host, nbd_session: XapiNbdSession, nbd_vdis: list[VDI]) -> None:
    """
    A connection to xapi-nbd must succeed while another connection doesn't read its replies,
    like a backup writing to a slow remote.
    """
    stalled_vdi, other_vdi = nbd_vdis

    with NbdClient.from_nbd_info(management_nbd_info(host, nbd_session, stalled_vdi)) as stalled_client:
        for i in range(STALLED_READS):
            stalled_client.send_read(i * BLOCK_SIZE, BLOCK_SIZE)
        # Let the replies fill the buffers up to tapdisk
        time.sleep(5)
        logging.info("tap-ctl list answers with a stalled NBD connection: %s", tap_ctl_list_answers(host))
        # Where the unread replies are: tapdisk -> xapi-nbd unix sockets, xapi-nbd -> runner TCP
        # sockets, and xapi-nbd memory
        logging.info("Sockets with a stalled NBD connection:\n%s", host.ssh(
            'ss -xp | grep -E "xapi-nbd|tapdisk"; ss -tnp "sport = :10809";'
            ' grep VmRSS /proc/$(pidof xapi-nbd)/status', check=False))

        info = management_nbd_info(host, nbd_session, other_vdi)
        timed_out = False
        try:
            with NbdClient.from_nbd_info(info, timeout=30) as client:
                client.read(0, BLOCK_SIZE)
        except TimeoutError:
            timed_out = True
        if timed_out:
            pytest.fail(
                "NBD connection got no answer from xapi-nbd in 30 s while another connection was stalled"
                f" (tap-ctl list answers: {tap_ctl_list_answers(host)})"
            )
        assert tap_ctl_list_answers(host), "tap-ctl list hangs"

def tapdisk_nbd_socket(host: Host, vdi: VDI) -> str:
    """ The NBD server socket of the tapdisk serving vdi, which must be attached. """
    for line in host.ssh('tap-ctl list').splitlines():
        if vdi.uuid in line:
            match = re.search(r'pid=(\d+) minor=(\d+)', line)
            assert match, f"unexpected tap-ctl list output: {line}"
            return f'/var/run/blktap-control/nbd{match.group(1)}.{match.group(2)}'
    raise AssertionError(f"no tapdisk serves {vdi}")

# Runs in dom0: connects to the NBD server of a tapdisk, sends a read and doesn't read
# the reply, then checks that tapdisk still answers `tap-ctl list`.
NOT_READING_CLIENT = """
import socket
import struct
import subprocess
import time

sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.settimeout(30)
sock.connect('{socket_path}')
sock.recv(18, socket.MSG_WAITALL)  # NBDMAGIC, IHAVEOPT, server flags
sock.sendall(struct.pack('>I', 1))  # NBD_FLAG_C_FIXED_NEWSTYLE
sock.sendall(b'IHAVEOPT' + struct.pack('>II', 1, 0))  # NBD_OPT_EXPORT_NAME, default export
sock.recv(134, socket.MSG_WAITALL)  # export size, transmission flags, zeroes
sock.sendall(struct.pack('>IHHQQI', 0x25609513, 0, 0, 1, 0, {length}))  # NBD_CMD_READ
time.sleep(2)
try:
    subprocess.run(['tap-ctl', 'list'], stdout=subprocess.DEVNULL, timeout=10, check=True)
    print('answers')
except subprocess.TimeoutExpired:
    print('hangs')
finally:
    sock.close()
"""

def test_tapdisk_nbd_client_not_reading(host: Host, nbd_vdis: list[VDI]) -> None:
    """ A tapdisk NBD client not reading its replies must not block tapdisk. """
    vdi = nbd_vdis[0]
    dom0 = host.get_dom0_vm()
    dom0.connect_vdi(vdi)
    try:
        script = NOT_READING_CLIENT.format(socket_path=tapdisk_nbd_socket(host, vdi), length=BLOCK_SIZE)
        result = host.execute_script(script, shebang='python3')
    finally:
        dom0.disconnect_vdi(vdi)
    assert result == 'answers', "tap-ctl list hangs while an NBD client of tapdisk doesn't read its reply"
