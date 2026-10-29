from __future__ import annotations

import pytest

import json
import logging
import shlex

from lib.common import wait_for

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.common import Defer
    from lib.host import Host

# NBD exports of xapi-nbd, as used by the NBD backups of XO: every NBD connection to xapi-nbd
# (port 10809) plugs the VDI read-only in dom0, serves the reads, then unplugs and destroys its
# VBD when the connection ends. Since xapi 26.1.21-1.1 (patch "xapi-nbd: Use tapdisk's NBD server
# instead of accessing the block device"), xapi-nbd reads through the NBD server of the tapdisk
# (/var/run/blktap-control/nbd<pid>.<minor>) instead of the dom0 block device.
#
# Requirements:
# - an XCP-ng host whose xapi-nbd uses the NBD server of tapdisk (the test is skipped otherwise)
# - no network with the "nbd" purpose (TLS): the client of the test only talks plain NBD. The
#   test gives the "insecure_nbd" purpose to the management network for its duration.

# NBD client, run in dom0 against xapi-nbd on 127.0.0.1:10809 with a local xapi session.
# It opens N_CONNS connections to the export of a VDI, like XO does for a disk, and keeps
# READS_IN_FLIGHT reads of READ_SIZE pipelined on each, checking every reply. Then:
# - "abort": after WARMUP_SECS, all the connections are reset while reads are in flight;
# - "staggered": after WARMUP_SECS, the first connection is reset while the others keep reading
#   for KEEP_READING_SECS (they must go on), then the others are reset.
# Prints a JSON list: for each connection, the reads completed in total and after the first
# reset, and its error (null if none).
XAPI_NBD_CLIENT = r'''
import json, socket, struct, sys, threading, time
import XenAPI

vdi_uuid, scenario, n_conns = sys.argv[1], sys.argv[2], int(sys.argv[3])
READS_IN_FLIGHT = 8
READ_SIZE = 1024 * 1024
WARMUP_SECS = 3
KEEP_READING_SECS = 5

def recv(s, size):
    data = b''
    while len(data) < size:
        chunk = s.recv(size - len(data))
        if not chunk:
            raise EOFError("connection closed by xapi-nbd after %d/%d bytes" % (len(data), size))
        data += chunk
    return data

class Conn(threading.Thread):
    def __init__(self, export):
        super().__init__(daemon=True)
        self.export = export.encode()
        self.completed = 0
        self.completed_at_first_reset = None
        self.error = None
        self.reset = False
        self.ready = threading.Event()

    def handshake(self):
        self.s = socket.create_connection(('127.0.0.1', 10809), timeout=60)
        greeting = recv(self.s, 18)
        assert greeting[:16] == b'NBDMAGICIHAVEOPT', "unexpected greeting %r" % greeting
        self.s.sendall(struct.pack('>I', 1))  # client flags: fixed newstyle
        # IHAVEOPT, NBD_OPT_EXPORT_NAME: the VDI is plugged in dom0 before the reply
        self.s.sendall(struct.pack('>QII', 0x49484156454F5054, 1, len(self.export)) + self.export)
        size, _ = struct.unpack('>QH', recv(self.s, 10))
        recv(self.s, 124)  # zeroes, as the client didn't send NBD_FLAG_C_NO_ZEROES
        return size

    def send_read(self, handle):
        offset = handle % self.blocks * READ_SIZE
        self.s.sendall(struct.pack('>IHHQQI', 0x25609513, 0, 0, handle, offset, READ_SIZE))

    def run(self):
        try:
            self.blocks = self.handshake() // READ_SIZE
            for handle in range(READS_IN_FLIGHT):
                self.send_read(handle)
            self.ready.set()
            expected = 0
            while True:
                magic, error, handle = struct.unpack('>IIQ', recv(self.s, 16))
                if (magic, error, handle) != (0x67446698, 0, expected):
                    raise AssertionError("unexpected reply %r, expected handle %d"
                                         % ((hex(magic), error, handle), expected))
                recv(self.s, READ_SIZE)
                self.completed += 1
                expected += 1
                self.send_read(expected + READS_IN_FLIGHT - 1)
        except Exception as e:
            if not self.reset:
                self.error = "%s: %s" % (type(e).__name__, e)
        finally:
            self.ready.set()

    def abort(self):
        """Reset the connection (RST, no FIN) with its reads in flight"""
        self.reset = True
        try:
            self.s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
            self.s.close()
        except (AttributeError, OSError):
            pass  # the handshake failed: already reported

session = XenAPI.xapi_local()
session.xenapi.login_with_password('root', '', '', 'xcp-ng-tests')
try:
    export = '/%s?session_id=%s' % (vdi_uuid, session._session)
    conns = [Conn(export) for _ in range(n_conns)]
    for conn in conns:
        conn.start()
    for conn in conns:
        conn.ready.wait(120)
    time.sleep(WARMUP_SECS)
    if scenario == 'abort':
        resets = [conns]
    elif scenario == 'staggered':
        resets = [conns[:1], conns[1:]]
    for i, group in enumerate(resets):
        if i:
            time.sleep(KEEP_READING_SECS)
        else:
            for conn in conns:
                conn.completed_at_first_reset = conn.completed
        for conn in group:
            conn.abort()
    for conn in conns:
        conn.join(30)
    print(json.dumps([{'completed': c.completed,
                       'after_first_reset': c.completed - (c.completed_at_first_reset or 0),
                       'error': c.error} for c in conns]))
finally:
    session.xenapi.session.logout()
'''

N_CONNS = 4
ITERATIONS = 5
VDI_SIZE = '256MiB'

def tapdisk_segfaults(host: Host) -> int:
    return int(host.ssh('dmesg | grep -c "tapdisk.*segfault" || true'))

@pytest.fixture
def insecure_nbd(host: Host, defer: Defer) -> None:
    """A network with the insecure_nbd purpose, for xapi-nbd to accept plain NBD connections."""
    purposes = {}
    for network in host.xe('network-list', minimal=True).split(','):
        purposes[network] = host.xe('network-param-get', {'uuid': network, 'param-name': 'purpose'})
    if any('insecure_nbd' in purpose for purpose in purposes.values()):
        return
    if any('nbd' in purpose for purpose in purposes.values()):
        pytest.skip("a network has the nbd purpose (TLS): the test client only talks plain NBD")
    network = host.xe('pif-list', {'host-uuid': host.uuid, 'management': True, 'params': 'network-uuid'},
                      minimal=True)
    host.xe('network-param-add', {'uuid': network, 'param-name': 'purpose', 'param-key': 'insecure_nbd'})
    defer(lambda: host.xe('network-param-remove',
                          {'uuid': network, 'param-name': 'purpose', 'param-key': 'insecure_nbd'}))

@pytest.fixture
def vdi_uuid(host: Host, defer: Defer) -> str:
    uuid = host.xe('vdi-create', {'sr-uuid': host.main_sr_uuid(), 'name-label': 'xcp-ng-tests xapi-nbd',
                                  'virtual-size': VDI_SIZE, 'type': 'user'})

    def destroy() -> None:
        # VBDs left behind by a tapdisk that couldn't be unplugged
        for vbd in filter(None, host.xe('vbd-list', {'vdi-uuid': uuid}, minimal=True).split(',')):
            host.xe('vbd-unplug', {'uuid': vbd}, check=False, force=True)
            host.xe('vbd-destroy', {'uuid': vbd}, check=False)
        host.xe('vdi-destroy', {'uuid': uuid})
    defer(destroy)
    return uuid

@pytest.mark.no_vm
@pytest.mark.parametrize('scenario', ['abort', 'staggered'])
def test_xapi_nbd_connections_reset_while_reading(
    host: Host, insecure_nbd: None, vdi_uuid: str, scenario: str
) -> None:
    """xapi-nbd connections reset with reads in flight: the others go on, the VBDs are unplugged."""
    # blktap commit "tapdisk: wait for the NBD requests in flight before closing an image":
    #   when its connection ends, xapi-nbd closes its connection to the NBD server of the tapdisk
    #   and unplugs its VBD. tap-ctl close then freed the NBD servers while some of their requests
    #   were still in flight, and tapdisk crashed when they completed: the VBD could no longer be
    #   unplugged, and with a hung tapdisk, every VBD operation of the host hung (tap-ctl list).
    if host.ssh_with_result('grep -q blktap-control /usr/sbin/xapi-nbd').returncode != 0:
        pytest.skip("xapi-nbd reads the dom0 block device, not the NBD server of tapdisk")

    segfaults = tapdisk_segfaults(host)
    client = shlex.join(['python3', '-c', XAPI_NBD_CLIENT, vdi_uuid, scenario, str(N_CONNS)])
    for i in range(ITERATIONS):
        logging.info(f"Iteration {i + 1}/{ITERATIONS}: {N_CONNS} NBD connections to {vdi_uuid}, "
                     f"reset while reading ({scenario})")
        conns = json.loads(host.ssh(client).splitlines()[-1])
        logging.info(f"NBD connections: {conns}")
        errors = [conn['error'] for conn in conns if conn['error']]
        assert not errors, f"NBD connections failed before being reset: {errors}"
        assert all(conn['completed'] > 0 for conn in conns), f"some NBD connections read nothing: {conns}"
        if scenario == 'staggered':
            assert all(conn['after_first_reset'] > 0 for conn in conns[1:]), \
                f"the other NBD connections stopped reading after the first one was reset: {conns}"

        wait_for(lambda: host.xe('vbd-list', {'vdi-uuid': vdi_uuid}, minimal=True) == '',
                 "Wait for xapi-nbd to unplug and destroy its VBDs", timeout_secs=120)
        assert host.ssh_with_result('timeout 30 tap-ctl list').returncode == 0, \
            "tap-ctl list hangs: a tapdisk no longer answers its control socket"
        assert vdi_uuid not in host.ssh('tap-ctl list'), \
            f"a tapdisk still serves VDI {vdi_uuid} after its VBDs were unplugged"
        assert tapdisk_segfaults(host) == segfaults, \
            f"tapdisk crashed:\n{host.ssh('dmesg | grep tapdisk.*segfault | tail -3')}"
