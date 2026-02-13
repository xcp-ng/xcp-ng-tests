from __future__ import annotations

import pytest

import json
import logging

from rpm_version import Evr  # type: ignore[import-untyped]

from data import HOST_FREE_NICS
from lib.common import PackageManagerEnum
from lib.host import Host
from lib.network import Network
from lib.tunnel import Tunnel
from lib.typing import JSONType
from lib.vlan import VLAN
from lib.vm import VM
from lib.xo import xo_cli

from typing import Generator, Literal

@pytest.fixture(scope='package')
def host_no_sdn_controller(host: Host) -> None:
    """ An XCP-ng with no SDN controller. """
    if host.xe('sdn-controller-list', minimal=True):
        pytest.fail("This test requires an XCP-ng with no SDN controller")

@pytest.fixture(scope='package')
def hosts_with_traffic_rules(hosts_with_xo: list[Host]) -> Generator[list[Host], None, None]:
    """A list of XCP-ng hosts with proper traffic rules configuration."""
    hosts = hosts_with_xo

    # check XO: check sdn-controller plugin (loaded + minimal version)
    minimal = Evr.parse("1.3.0")

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
            if minimal > Evr.parse(version):
                pytest.fail(f"This test requires XO with at least sdn-controller version {minimal}")
        else:
            pytest.fail("This test requires XO with sdn-controller plugin loaded")

    if not plugin_found:
        pytest.fail("This test requires XO with sdn-controller plugin")

    # check host: xcp-ng-xapi-plugins minimal version
    minimal = Evr.parse("xcp-ng-xapi-plugins-1.17.0")

    def host_with_xcp_ng_xapi_plugins(host: Host):
        # get the package version
        packages = json.loads(host.xe('host-call-plugin', {
            'host-uuid': host.uuid,
            'plugin': 'updater.py',
            'fn': 'query_installed',
            'args:packages': 'xcp-ng-xapi-plugins',
        }, minimal=True))

        return minimal <= Evr.parse(packages.get('xcp-ng-xapi-plugins', ''))

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
    elif pkg_mgr == PackageManagerEnum.YUM:
        vm.ssh("yum install tcpdump")
    elif pkg_mgr == PackageManagerEnum.DNF:
        vm.ssh("dnf install tcpdump")
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
@pytest.fixture(scope='function')
def empty_network(host: Host) -> Generator[Network, None, None]:
    net = host.create_network(label="empty_network for tests")

    yield net

    for vif_uuid in net.vif_uuids():
        host.xe("vif-unplug", {
            'uuid': vif_uuid,
        })

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
    prepare: dict[str, Literal[True]] = {}
    if not host.is_package_installed("openvswitch-ipsec"):
        prepare["installed-openvswitch-ipsec"] = True
        host.yum_install(["openvswitch-ipsec"])
    if not host.service_started("ipsec"):
        prepare["service-ipsec"] = True
        host.ssh("systemctl start ipsec")
    if not host.service_started("openvswitch-ipsec"):
        prepare["service-openvswitch-ipsec"] = True
        host.ssh("systemctl start openvswitch-ipsec")

    # create a tunnel over the management PIF
    tunnel_device = host.management_pif().device()

    logging.info(f"tunnel: resolve PIF on {host.hostname_or_ip} using \
        {[(pif.network_uuid(), pif.device()) for pif in host.pifs()]}")

    # we could have several pifs on one device (due to VLANs for example)
    pifs = [pif for pif in host.pifs(device=tunnel_device) if pif.ip_configuration_mode() != "None"]
    if len(pifs) == 0:
        pytest.fail(f"'tunnel' fixture requires tunnel_device={tunnel_device} to have configured IP")

    # use the first usable pif
    pif = pifs[0]

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
        network_uuids.add(tunnel.access_pif().network_uuid())

        # destroy the tunnel
        tunnel.destroy()

    # destroy networks associated to destroyed tunnels
    for network_uuid in network_uuids:
        Network(host, network_uuid).destroy()

    # remove installed dependencies
    if "service-openvswitch-ipsec" in prepare:
        host.ssh("systemctl stop openvswitch-ipsec")
    if "service-ipsec" in prepare:
        host.ssh("systemctl stop ipsec")
    if "installed-openvswitch-ipsec" in prepare:
        host.yum_remove(["openvswitch-ipsec"])

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
