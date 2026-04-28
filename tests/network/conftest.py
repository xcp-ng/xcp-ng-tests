from __future__ import annotations

import pytest

import logging

from data import HOST_FREE_NICS
from lib.common import PackageManagerEnum
from lib.host import Host
from lib.network import Network
from lib.tunnel import Tunnel
from lib.vlan import VLAN
from lib.vm import VM
from lib.xo import xo_cli

from typing import Generator

@pytest.fixture(scope='package')
def host_no_sdn_controller(host: Host) -> None:
    """ An XCP-ng with no SDN controller. """
    if host.xe('sdn-controller-list', minimal=True):
        pytest.fail("This test requires an XCP-ng with no SDN controller")


# a clone of imported_vm in which we've added tcpdump
# not to be used by tests directly
@pytest.fixture(scope='module')
def vm_with_tcpdump_scope_module(imported_vm: VM):
    logging.info("Preparing VM with tcpdump installed")
    vm = imported_vm.clone()
    vm.start()
    vm.wait_for_vm_running_and_ssh_up()

    # install tcpdump
    pkg_mgr = vm.detect_package_manager()
    if pkg_mgr == PackageManagerEnum.APK:
        vm.ssh("apk add tcpdump")
    elif pkg_mgr == PackageManagerEnum.APT_GET:
        vm.ssh("apt-get install tcpdump")
    elif pkg_mgr == PackageManagerEnum.RPM:
        # XXX assume yum for now
        vm.ssh("yum install tcpdump")
    else:
        pytest.fail("Package manager '%s' not supported" % pkg_mgr)

    vm.shutdown(verify=True)
    yield vm
    vm.destroy()

@pytest.fixture(scope='function')
def vm_with_tcpdump_scope_function(vm_with_tcpdump_scope_module: VM):
    vm = vm_with_tcpdump_scope_module.clone()
    yield vm
    vm.destroy()


# ---- Bond ----
@pytest.fixture(scope='function')
def bond_lacp(host: Host, empty_network: Network):
    if len(HOST_FREE_NICS) < 2:
        pytest.fail("This fixture needs at least 2 free NICs")

    pifs = []
    logging.info(f"bond: resolve PIFs on {host.hostname_or_ip} using \
        {[(pif.network_uuid(), pif.param_get('device')) for pif in host.pifs()]}")
    for name in HOST_FREE_NICS[0:2]:
        [pif] = host.pifs(device=name)
        pifs.append(pif)

    bond = host.create_bond(empty_network, pifs, mode="lacp")
    yield bond
    bond.destroy()

@pytest.fixture(scope='function')
def bond_activebackup(host: Host, empty_network: Network):
    if len(HOST_FREE_NICS) < 2:
        pytest.fail("This fixture needs at least 2 free NICs")

    pifs = []
    logging.info(f"bond: resolve PIFs on {host.hostname_or_ip} using \
        {[(pif.network_uuid(), pif.param_get('device')) for pif in host.pifs()]}")
    for name in HOST_FREE_NICS[0:2]:
        [pif] = host.pifs(device=name)
        pifs.append(pif)

    bond = host.create_bond(empty_network, pifs, mode="active-backup")
    yield bond
    bond.destroy()

@pytest.fixture(scope='function')
def bond_balanceslb(host: Host, empty_network: Network):
    if len(HOST_FREE_NICS) < 2:
        pytest.fail("This fixture needs at least 2 free NICs")

    pifs = []
    logging.info(f"bond: resolve PIFs on {host.hostname_or_ip} using \
        {[(pif.network_uuid(), pif.param_get('device')) for pif in host.pifs()]}")
    for name in HOST_FREE_NICS[0:2]:
        [pif] = host.pifs(device=name)
        pifs.append(pif)

    bond = host.create_bond(empty_network, pifs, mode="balance-slb")
    yield bond
    bond.destroy()


# ---- Network ----
@pytest.fixture(scope='module')
def empty_network(host: Host) -> Generator[Network, None, None]:
    net = host.create_network(label="empty_network for tests")
    yield net
    net.destroy()


# ---- Tunnel ----
@pytest.fixture(params=["gre", "vxlan"])
def tunnel_protocol(request: pytest.FixtureRequest) -> str:
    return request.param

@pytest.fixture(params=[False, True])
def tunnel_encryption(request: pytest.FixtureRequest) -> bool:
    return request.param

@pytest.fixture
def tunnel(
    hosts_with_xo: list[Host],
    tunnel_protocol: str, tunnel_encryption: bool,
) -> Generator[Tunnel, None, None]:
    host = hosts_with_xo[0]

    # check system requirements
    if not host.is_package_installed("openvswitch-ipsec"):
        pytest.fail("'tunnel' fixture requires configuration, see https://docs.xen-orchestra.com/sdn_controller")

    # create a tunnel over the management PIF
    tunnel_device = host.management_pif().device()

    logging.info(f"tunnel: resolve PIF on {host.hostname_or_ip} using \
        {[(pif.network_uuid(), pif.device()) for pif in host.pifs()]}")

    [pif] = host.pifs(device=tunnel_device)
    if pif.ip_configuration_mode() == "None":
        pytest.fail(f"'tunnel' fixture requires tunnel_device={tunnel_device} to have configured IP")

    existing_tunnels = [t.uuid for t in host.tunnels()]
    logging.info(f"tunnel: existing tunnels: {existing_tunnels}")

    xo_cli('sdnController.createPrivateNetwork', {
        'poolIds': f"json:[\"{host.pool.uuid}\"]",
        'pifIds': f"json:[\"{pif.uuid}\"]",
        'name': 'test-tunnel',
        'description': 'tunnel for test',
        'encapsulation': tunnel_protocol,
        'encrypted': 'true' if tunnel_encryption else 'false',
    })

    # sdnController.createPrivateNetwork might have created several Tunnel (one per host)
    # so get all created Tunnel
    created_tunnels = list(set([t.uuid for t in host.tunnels()]) - set(existing_tunnels))
    logging.info(f"tunnel: created tunnels: {created_tunnels}")

    # yield only the first tunnel
    yield Tunnel(host, created_tunnels[0])

    # teardown created_tunnels (and associated networks)
    network_uuids: set[str] = set()

    for tunnel_uuid in created_tunnels:
        tunnel = Tunnel(host, tunnel_uuid)

        # get network linked to the tunnel
        network_uuids.add(tunnel.access_PIF().network_uuid())

        # destroy the tunnel
        tunnel.destroy()

    # destroy networks associated to destroyed tunnels
    for network_uuid in network_uuids:
        Network(host, network_uuid).destroy()


# ---- VLAN ----
@pytest.fixture
def vlan(host: Host, empty_network: Network) -> Generator[VLAN, None, None]:
    logging.info(f"vlan: resolve PIF on {host.hostname_or_ip} using \
        {[(pif.network_uuid(), pif.param_get('device')) for pif in host.pifs()]}")

    if len(HOST_FREE_NICS) < 1:
        pytest.fail("This fixture needs at least 1 free NICs")

    # randomly chosen tag
    vlan_tag = 42

    [pif] = host.pifs(device=HOST_FREE_NICS[0])
    vlan = host.create_vlan(empty_network, pif, vlan_tag)
    yield vlan
    vlan.destroy()
