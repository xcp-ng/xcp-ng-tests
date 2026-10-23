from __future__ import annotations

import pytest

import logging
import socket

from lib.common import MiB, safe_split, wait_for
from lib.host import Host
from lib.nbd import XapiNbdSession
from lib.network import Network
from lib.sr import SR
from lib.vdi import VDI

from typing import Generator

NBD_VDI_SIZE = 256 * MiB
NBD_PORT = 10809

def nbd_port_open(host: Host) -> bool:
    try:
        socket.create_connection((host.hostname_or_ip, NBD_PORT), timeout=5).close()
        return True
    except OSError:
        return False

@pytest.fixture(scope='module')
def nbd_network(host: Host) -> Generator[Network, None, None]:
    """ The management network, with the `nbd` purpose: xapi-nbd only serves the networks having it. """
    network = Network(host, host.management_network())
    purposes = safe_split(network.param_get('purpose'), '; ')
    if 'insecure_nbd' in purposes:
        pytest.skip("the management network has the insecure_nbd purpose, these tests use TLS")
    added = 'nbd' not in purposes
    if added:
        logging.info("Add the nbd purpose to network %s", network.uuid)
        network.param_add('purpose', 'nbd')
        # xapi opens the firewall for xapi-nbd a few seconds later
        wait_for(lambda: nbd_port_open(host), "Wait for xapi-nbd to accept connections", timeout_secs=60,
                 retry_delay_secs=1)
    yield network
    if added:
        logging.info("Remove the nbd purpose from network %s", network.uuid)
        network.param_remove('purpose', 'nbd')

@pytest.fixture(scope='module')
def nbd_session(host: Host) -> Generator[XapiNbdSession, None, None]:
    session = XapiNbdSession(host)
    yield session
    session.logout()

@pytest.fixture(scope='module')
def nbd_vdis(host: Host) -> Generator[list[VDI], None, None]:
    """ Two VDIs on the main SR of the host. """
    sr = SR(host.main_sr_uuid(), host.pool)
    vdis = [sr.create_vdi(f'NBD-test-VDI-{i}', virtual_size=NBD_VDI_SIZE) for i in range(2)]
    yield vdis
    for vdi in vdis:
        vdi.destroy()

@pytest.fixture()
def tapdisk_control_check(host: Host) -> Generator[None, None, None]:
    """
    Leave the host usable when a test deadlocks tapdisk with xapi-nbd.

    A stuck `tap-ctl list` (which also blocks xapi-nbd) is killed after the test, which
    breaks the deadlock.
    """
    yield
    if host.ssh_with_result('timeout 10 tap-ctl list').returncode == 0:
        return
    logging.error("tap-ctl list hangs on %s: killing the stuck tap-ctl processes", host)
    host.ssh("pkill -f '^/usr/sbin/tap-ctl list'", check=False)
