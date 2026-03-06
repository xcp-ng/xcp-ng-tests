import pytest

import logging

from lib.host import Host
from lib.network import Network

from typing import Generator

@pytest.fixture(scope='package')
def host_no_sdn_controller(host: Host):
    """ An XCP-ng with no SDN controller. """
    if host.xe('sdn-controller-list', minimal=True):
        pytest.skip("This test requires an XCP-ng with no SDN controller")


@pytest.fixture(scope='module')
def empty_network(host: Host) -> Generator[Network, None, None]:
    try:
        net = host.create_network(label="empty_network for tests")
        yield net
    finally:
        net.destroy()

@pytest.fixture(params=[])
def bond_devices(request: pytest.FixtureRequest) -> list[str]:
    return request.param

@pytest.fixture(params=["lacp"])
def bond_mode(request: pytest.FixtureRequest) -> str:
    return request.param

@pytest.fixture
def bond(host: Host, empty_network: Network, bond_devices: list[str], bond_mode: str):
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
