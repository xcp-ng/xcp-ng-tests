from __future__ import annotations

import pytest

import json
import logging
import re

from data import HOST_FREE_NICS
from lib.common import PackageManagerEnum
from lib.host import Host
from lib.network import Network
from lib.tunnel import Tunnel
from lib.typing import JSONType
from lib.vlan import VLAN
from lib.vm import VM
from lib.xo import xo_cli

from typing import Generator

@pytest.fixture(scope='package')
def host_no_sdn_controller(host: Host) -> None:
    """ An XCP-ng with no SDN controller. """
    if host.xe('sdn-controller-list', minimal=True):
        pytest.fail("This test requires an XCP-ng with no SDN controller")

RE_digits = re.compile(r'(\d+)')

def compare_versions(a: str, b: str) -> int:
    """
    Return 1 if a > b, -1 if a < b, 0 if equal.
    Extracts successive digit groups from each string and compares them as integers.
    Non-digit separators are ignored. Missing groups are treated as 0.
    """
    match_a = RE_digits.search(a)
    match_b = RE_digits.search(b)

    while match_a or match_b:
        num_a = int(match_a.group(1)) if match_a else 0
        num_b = int(match_b.group(1)) if match_b else 0

        if num_a != num_b:
            return 1 if num_a > num_b else -1

        # advance positions past the matched groups
        pos_a = (match_a.end() if match_a else len(a))
        pos_b = (match_b.end() if match_b else len(b))

        match_a = RE_digits.search(a, pos_a)
        match_b = RE_digits.search(b, pos_b)

    return 0


@pytest.fixture(scope='package')
@pytest.mark.usefixtures('host_at_least_8_3')
def hosts_with_traffic_rules(hosts_with_xo: list[Host]) -> Generator[list[Host], None, None]:
    """A list of XCP-ng hosts with proper traffic rules configuration."""
    hosts = hosts_with_xo

    # check XO: check sdn-controller plugin (loaded + minimal version)
    minimal = "1.3.0"

    plugin_found = False
    plugins = xo_cli('plugin.get', use_json=True)
    assert isinstance(plugins, list)
    for plugin in plugins:
        assert isinstance(plugin, dict)
        if plugin.get('id') != 'sdn-controller':
            continue

        plugin_found = True
        loaded = plugin.get('loaded', False)
        assert isinstance(loaded, bool)
        if loaded:
            version = plugin.get('version', '')
            assert isinstance(version, str)
            if compare_versions(minimal, version) > 0:
                pytest.fail(f"This test requires XO with at least sdn-controller version {minimal}")
        else:
            pytest.fail("This test requires XO with sdn-controller plugin loaded")

    if not plugin_found:
        pytest.fail("This test requires XO with sdn-controller plugin")

    # check host: xcp-ng-xapi-plugins minimal version
    minimal = "1.16.0"

    def host_with_xcp_ng_xapi_plugins(host: Host):
        # get the package version
        packages = json.loads(host.xe('host-call-plugin', {
            'host-uuid': host.uuid,
            'plugin': 'updater.py',
            'fn': 'query_installed',
            'args:packages': 'xcp-ng-xapi-plugins',
        }, minimal=True))

        return compare_versions(minimal, packages.get('xcp-ng-xapi-plugins', '')) <= 0

    hosts = list(filter(host_with_xcp_ng_xapi_plugins, hosts))
    if len(hosts) == 0:
        pytest.fail(f"This test requires hosts with at least xcp-ng-xapi-plugins version {minimal}")

    # check XO: check sdn-controller configuration: should be using xapi-plugin method for OpenFlow rules
    def host_with_xapiplugin(host: Host) -> bool:
        # the key 'xo:sdn-controller:of-method' is present since cycle XO 6.5c 2026-05-14 (xo-lite v0.21.0)
        of_method = host.pool.param_get(
            'other-config',
            key='xo:sdn-controller:of-method',
            accept_unknown_key=True,
        ) or 'channel'

        return of_method == 'xapi-plugin'

    hosts = list(filter(host_with_xapiplugin, hosts))
    if len(hosts) == 0:
        pytest.fail("This test requires XO to use of-method=xapi-plugin "
                    "(see https://docs.xen-orchestra.com/xo5/configuration#sdn-controller-mode)")

    yield hosts


# a clone of imported_vm in which we've added tcpdump
# not to be used by tests directly
@pytest.fixture(scope='module')
def vm_with_tcpdump_scope_module(imported_vm: VM):
    logging.info("Preparing VM with tcpdump installed")
    vm = imported_vm.clone(name=f"{imported_vm.name()} with tcpdump")
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
    vm = vm_with_tcpdump_scope_module.clone(name=f"{vm_with_tcpdump_scope_module.name()} for tests")
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
