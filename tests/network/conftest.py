from __future__ import annotations

import pytest

import logging

from lib.bond import Bond
from lib.host import Host
from lib.network import Network
from lib.tunnel import Tunnel
from lib.vlan import VLAN
from lib.xo import xo_cli

from typing import Generator

@pytest.fixture(scope='package')
def host_no_sdn_controller(host: Host):
    """ An XCP-ng with no SDN controller. """
    if host.xe('sdn-controller-list', minimal=True):
        pytest.skip("This test requires an XCP-ng with no SDN controller")


# ---- Bond ----
@pytest.fixture(params=[])
def bond_devices(request: pytest.FixtureRequest) -> list[str]:
    return request.param

@pytest.fixture(params=["lacp"])
def bond_mode(request: pytest.FixtureRequest) -> str:
    return request.param

@pytest.fixture
def bond(host: Host, empty_network: Network, bond_devices: list[str], bond_mode: str) -> Generator[Bond, None, None]:
    pifs = []
    logging.info(f"bond: resolve PIFs on {host.hostname_or_ip} using \
        {[(pif.network_uuid(), pif.param_get('device')) for pif in host.pifs()]}")
    for name in bond_devices:
        [pif] = host.pifs(device=name)
        pifs.append(pif)

    bond = host.create_bond(empty_network, pifs, mode=bond_mode)
    try:
        yield bond
    finally:
        bond.destroy()


# ---- Network ----
@pytest.fixture(scope='module')
def empty_network(host: Host) -> Generator[Network, None, None]:
    try:
        net = host.create_network(label="empty_network for tests")
        yield net
    finally:
        net.destroy()


# ---- Tunnel ----
@pytest.fixture(params=["eth0"])
def tunnel_device(request: pytest.FixtureRequest) -> str:
    return request.param

@pytest.fixture(params=["gre", "vxlan"])
def tunnel_protocol(request: pytest.FixtureRequest) -> str:
    return request.param

@pytest.fixture(params=[False, True])
def tunnel_encryption(request: pytest.FixtureRequest) -> bool:
    return request.param

@pytest.fixture
def tunnel(
    connected_hosts_with_xo: list[Host],
    tunnel_device: str, tunnel_protocol: str, tunnel_encryption: bool,
) -> Generator[Tunnel, None, None]:
    host = connected_hosts_with_xo[0]

    # check system requirements
    if not host.is_package_installed("openvswitch-ipsec"):
        pytest.skip("'tunnel' fixture requires configuration, see https://docs.xen-orchestra.com/sdn_controller")

    logging.info(f"tunnel: resolve PIF on {host.hostname_or_ip} using \
        {[(pif.network_uuid(), pif.param_get('device')) for pif in host.pifs()]}")

    [pif] = host.pifs(device=tunnel_device)
    if pif.ip_configuration_mode() == "None":
        pytest.skip(f"'tunnel' fixture requires tunnel_device={tunnel_device} to have configured IP")

    xo_cli('sdnController.createPrivateNetwork', {
        'poolIds': f"json:[\"{host.pool.uuid}\"]",
        'pifIds': f"json:[\"{pif.uuid}\"]",
        'name': 'test-tunnel',
        'description': 'tunnel for test',
        'encapsulation': tunnel_protocol,
        'encrypted': 'true' if tunnel_encryption else 'false',
    })
    tunnel = None
    network = None
    try:
        # get Tunnel from PIF
        tunnel_uuid = host.xe('tunnel-list', {
            'transport-PIF': pif.uuid,
        }, minimal=True)
        tunnel = Tunnel(host, tunnel_uuid)

        # get Network from Tunnel
        network_uuid = tunnel.access_PIF().network_uuid()
        network = Network(host, network_uuid)

        yield tunnel
    finally:
        if tunnel is not None:
            tunnel.destroy()

        if network is not None:
            # sdnController.createPrivateNetwork might have create several Tunnel (one per host)
            # so get all Tunnel attached to Network and destroy them
            for pif_uuid in network.pif_uuids():
                tunnel_uuid = host.xe('tunnel-list', {
                    'access-PIF': pif_uuid,
                }, minimal=True)
                tunnel = Tunnel(host, tunnel_uuid)
                tunnel.destroy()

            # finally destroy the Network
            network.destroy()


# ---- VLAN ----
@pytest.fixture(params=["eth0"])
def vlan_device(request: pytest.FixtureRequest) -> str:
    return request.param

@pytest.fixture(params=[0])
def vlan_tag(request: pytest.FixtureRequest) -> int:
    return request.param

@pytest.fixture
def vlan(host: Host, empty_network: Network, vlan_tag: int, vlan_device: str) -> Generator[VLAN, None, None]:
    logging.info(f"vlan: resolve PIF on {host.hostname_or_ip} using \
        {[(pif.network_uuid(), pif.param_get('device')) for pif in host.pifs()]}")

    [pif] = host.pifs(device=vlan_device)
    vlan = host.create_vlan(empty_network, pif, vlan_tag)
    try:
        yield vlan
    finally:
        vlan.destroy()
