import pytest

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
        net = Network.create(host, label="empty_network for tests")
        yield net
    finally:
        net.destroy()
