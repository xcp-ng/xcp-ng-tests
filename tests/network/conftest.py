import pytest

from lib.host import Host

@pytest.fixture(scope='package')
def host_no_sdn_controller(host: Host) -> None:
    """ An XCP-ng with no SDN controller. """
    if host.xe('sdn-controller-list', minimal=True):
        pytest.skip("This test requires an XCP-ng with no SDN controller")
