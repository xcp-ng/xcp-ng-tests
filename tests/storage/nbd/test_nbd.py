from __future__ import annotations

import pytest

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
# Both tests reproduce a deadlock between tapdisk and xapi-nbd, seen with XO backups
# overlapping on a host. tapdisk sent each NBD reply with a blocking send() from its event
# loop, so a peer not reading its replies froze the whole tapdisk, `tap-ctl list` included.
# xapi-nbd runs `tap-ctl list` synchronously when a client connects, and doesn't read its
# other connections meanwhile: the tapdisk sending them a reply never answers.

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

def first_nbd_info(session: XapiNbdSession, vdi: VDI) -> NbdInfo:
    infos = session.nbd_info(vdi)
    assert infos, f"VDI.get_nbd_info returned nothing for {vdi}"
    return infos[0]

@pytest.mark.usefixtures("nbd_network", "tapdisk_control_check")
def test_xapi_nbd_connection_while_reading(host: Host, nbd_session: XapiNbdSession, nbd_vdis: list[VDI]) -> None:
    """ A connection to xapi-nbd must succeed while another connection is reading. """
    reading_vdi, other_vdi = nbd_vdis

    with NbdClient.from_nbd_info(first_nbd_info(nbd_session, reading_vdi)) as reading_client:
        streamer = NbdStreamer(reading_client)
        streamer.start()
        try:
            wait_for(lambda: streamer.bytes_read >= 64 * MiB or streamer.error is not None,
                     "Wait for the first NBD connection to be reading", timeout_secs=60, retry_delay_secs=1)
            assert streamer.error is None, f"the first NBD connection failed: {streamer.error!r}"

            for i in range(5):
                info = first_nbd_info(nbd_session, other_vdi)
                start = time.monotonic()
                timed_out = False
                try:
                    with NbdClient.from_nbd_info(info, timeout=30) as client:
                        client.read(0, BLOCK_SIZE)
                except TimeoutError:
                    timed_out = True
                if timed_out:
                    pytest.fail(
                        f"NBD connection {i} got no answer from xapi-nbd in 30 s while another connection was"
                        f" reading (tap-ctl list answers: {tap_ctl_list_answers(host)})"
                    )
                logging.info("NBD connection %d: connected and read %d bytes in %.2f s",
                             i, BLOCK_SIZE, time.monotonic() - start)

            read_before = streamer.bytes_read
            time.sleep(2)
            assert streamer.error is None, f"the first NBD connection failed: {streamer.error!r}"
            assert streamer.bytes_read > read_before, "the first NBD connection stopped reading"
            assert tap_ctl_list_answers(host), "tap-ctl list hangs"
        finally:
            streamer.stop()

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
