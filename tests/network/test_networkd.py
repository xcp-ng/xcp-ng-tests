from __future__ import annotations

import pytest

import logging
from datetime import datetime

from lib.common import Defer, XeParams, wait_for
from lib.host import Host
from lib.network import Network
from lib.pif import PIF
from lib.vlan import VLAN

from typing import Literal, Protocol

# Requirements:
# - one XCP-ng host (--hosts) >= 8.2 - with at least 2 PIFs per host.
#   The one used for management network will not be changed.

def host_get_test_pifs(host: Host) -> list[PIF]:
    """
    Returns the PIFs of the host that it is suitable for reconfiguration.
    """
    all_pifs = host.pifs()
    avoided_uuids: set[str] = set()

    # do not pick management interface
    avoided_uuids.add(host.management_pif().uuid)

    # do not pick unmanaged PIFs
    avoided_uuids.update([pif.uuid for pif in all_pifs if not pif.is_managed()])

    # do not pick parent interfaces used by VLANs
    # (assume trunks aren't safe for direct usage)
    for pif in all_pifs:
        if pif.vlan() is None:
            continue
        vlan_uuid = pif.param_get('vlan-master-of')
        if vlan_uuid is None:
            continue
        vlan = VLAN(host, vlan_uuid)
        # avoid the trunk (the interface with tagged packets)
        avoided_uuids.add(vlan.tagged_pif().uuid)

    # do not pick interfaces used for Tunnels
    for tunnel in host.tunnels():
        # transport_pif changes would interfere with Tunnel
        avoided_uuids.add(tunnel.transport_pif().uuid)

        if tunnel.param_get('status') == 'active: false':
            # avoid access_pif if the Tunnel isn't active
            avoided_uuids.add(tunnel.access_pif().uuid)

    # get the list of possible PIFs
    all_uuids: set[str] = set([pif.uuid for pif in all_pifs])
    possible_uuids: set[str] = all_uuids.difference(avoided_uuids)

    logging.debug(f"Possible PIFs for testing: {possible_uuids})")
    return [PIF(uuid, host) for uuid in possible_uuids]

def pif_ipv4_state(pif: PIF, state: XeParams | None) -> XeParams:
    """
    Sets the IPv4 configuration for the PIF using state (if provided), returning the previous state.
    """
    # safebelt: do not operate on management interface
    assert not pif.is_management()

    previous: XeParams = {
        'mode': pif.param_get('IP-configuration-mode'),
        'IP': pif.param_get('IP'),
        'netmask': pif.param_get('netmask'),
        'gateway': pif.param_get('gateway'),
        'DNS': pif.param_get('DNS'),
    }

    if state is not None:
        logging.info(
            f"Reconfiguring to '{state['mode']}': PIF {pif.uuid} (on '{pif.param_get('network-name-label') or '?'}')",
        )
        state['uuid'] = pif.uuid
        pif.host.xe('pif-reconfigure-ip', state)

    return previous

# --------------------------------------------------------------------
class DHCPClient(Protocol):
    pif: PIF

    def is_running(self) -> bool:
        """
        Returns if the DHCP client is running for the PIF.
        """
        ...

    def is_stopped(self) -> bool:
        """
        Returns if the DHCP client is properly stopped for the PIF.
        """
        ...

    def assert_state(self, state: Literal["running"] | Literal["stopped"]) -> None:
        wait_for(
            self.is_running if state == "running" else self.is_stopped,
            msg=f"Waiting for DHCP client to be {state}",
            timeout_secs=5,
        )


# --------------------------------------------------------------------

class Dhclient(DHCPClient):
    """
    DHCPClient implementation for XenAPI running dhclient.
    """

    def __init__(self, pif: PIF, mode: Literal["4"] | Literal["6"]):
        self.pif = pif
        self.bridge = Network(pif.host, pif.network_uuid()).bridge()

        if mode == "4":
            self.prefix = ""
        else:
            self.prefix = "6"

    def pid_file(self) -> str:
        return f"/var/run/dhclient{self.prefix}-{self.bridge}.pid"

    def leases_file(self) -> str:
        return f"/var/lib/xcp/dhclient{self.prefix}-{self.bridge}.leases"

    def process_is_running(self) -> bool:
        """
        Returns if dhclient process is properly running.
        Read the pid_file, check a process is running with this PID.
        """
        host = self.pif.host

        # has a pidfile
        pidfile = self.pid_file()
        if not host.file_exists(pidfile):
            return False

        # check process is running
        pid = host.ssh(f"cat {pidfile}")
        return host.file_exists(f"/proc/{pid}", regular_file=False)

    def leases(self) -> list[dict[str, str]]:
        """
        Returns the list of active and not expired leases.
        """
        host = self.pif.host
        leasesfile = self.leases_file()

        if not host.file_exists(leasesfile):
            return []

        now = datetime.now()

        leases: dict[str, dict[str, str]] = {}
        lease: dict[str, str] = {}

        # simple parser of leasesfile
        for line in host.ssh(f"cat {leasesfile}").splitlines():
            if line == "lease {":
                lease = {}
                continue

            elif line == "}":
                # keep only the last lease for each interface
                # (the file is append-only)
                leases[lease['interface']] = lease

                # do not keep expired lease
                if now > datetime.fromisoformat(lease['expire']):
                    del leases[lease['interface']]

                continue

            # split in words (removing last ';' and spaces)
            words: list[str] = line[:-1].strip(' ').split(' ')

            if words[0] == 'option':
                key = f"option:{words[1]}"
                value = words[2].strip('"')

            elif words[0] in ["renew", "rebind", "expire"]:
                # normalize date to isoformat
                key = words[0]
                value = datetime.strptime(f"{words[2]} {words[3]}", "%Y/%m/%d %H:%M:%S").isoformat()

            elif len(words) == 1:
                # some keys has no value (like 'bootp')
                key = words[0]
                value = ''

            else:
                key = words[0]
                value = words[1].strip('"')

            lease[key] = value

        return list(leases.values())

    def is_running(self) -> bool:
        host = self.pif.host

        # check processus
        if not self.process_is_running():
            logging.debug("Dhclient.is_running: process is not running")
            return False

        # check leases_file
        leases = self.leases()
        if len(leases) == 0:
            logging.debug("Dhclient.is_running: no valid leases (no DHCP server replied ?)")
            assert self.pif.param_get('IP') == ""

        # check that each lease is used
        for lease in leases:
            # get infos from the lease
            dev = lease.get('interface')
            ip = lease.get('fixed-address')
            routers = lease.get('option:routers', '').split(',')

            # check ip (if available)
            if ip and host.ssh_with_result(f"ip address show dev {dev} | grep -F {ip}").returncode != 0:
                logging.debug(f"Dhclient.is_running: IP '{ip}' not present on interface '{dev}'")
                return False

            # check routers (if available)
            for gw in routers:
                if host.ssh_with_result(f"ping -c1 -w1 {gw}").returncode != 0:
                    logging.debug(f"Dhclient.is_running: unable to reach remote IP: {gw}")
                    return False

        return True

    def is_stopped(self) -> bool:
        # check processus
        if self.process_is_running():
            logging.debug("Dhclient.is_stopped: process is still running")
            return False

        # check leases_file
        leases = self.leases()
        if len(leases) != 0:
            logging.debug(f"Dhclient.is_stopped: has valid leases ({len(leases)})")
            return False

        return True


# --------------------------------------------------------------------

@pytest.mark.no_vm
class TestDHCPv4Lifecycle:

    def test_acquire(self, defer: Defer, host: Host):
        # Acquire: set interface to DHCP
        # - dhcp client process running,
        # - lease recorded (if some DHCP server replied).

        test_pifs = host_get_test_pifs(host)
        if len(test_pifs) == 0:
            pytest.fail("unable to found suitable PIF for testing")

        for test_pif in test_pifs:
            logging.info(f"Testing on PIF {test_pif.uuid} (on '{test_pif.param_get('network-name-label') or '?'}')")

            # xapi only supports Dhclient (for now)
            dhcp = Dhclient(test_pif, "4")

            # reconfigure the PIF to DHCP
            # if it is already DHCP, it isn't a problem as we will check it is properly setted
            previous_state = pif_ipv4_state(test_pif, {'mode': 'DHCP'})
            defer(lambda pif=test_pif, state=previous_state: pif_ipv4_state(pif, state))  # type: ignore[misc]

            dhcp.assert_state("running")

    def test_release(self, defer: Defer, host: Host):
        # Stop releases:
        # - switch DHCP → static/none → process gone
        # - lease released
        # - dhclient cleanup review + address flushed, pidfile file removed.

        test_pifs = host_get_test_pifs(host)
        if len(test_pifs) == 0:
            pytest.fail("unable to found suitable PIF for testing")

        for test_pif in test_pifs:
            logging.info(f"Testing on PIF {test_pif.uuid} (on '{test_pif.param_get('network-name-label') or '?'}')")

            # xapi only supports Dhclient (for now)
            dhcp = Dhclient(test_pif, "4")

            # get current state (and ensure we restore it at end)
            previous_state = pif_ipv4_state(test_pif, None)
            defer(lambda pif=test_pif, state=previous_state: pif_ipv4_state(pif, state))  # type: ignore[misc]

            # DHCP -> None
            pif_ipv4_state(test_pif, {'mode': 'DHCP'})
            dhcp.assert_state("running")
            pif_ipv4_state(test_pif, {'mode': 'None'})
            dhcp.assert_state("stopped")

            # DHCP -> Static
            pif_ipv4_state(test_pif, {'mode': 'DHCP'})
            dhcp.assert_state("running")
            pif_ipv4_state(test_pif, {
                'mode': 'Static',
                'IP': '192.168.4.2',
                'netmask': '255.255.255.0',
            })
            dhcp.assert_state("stopped")
