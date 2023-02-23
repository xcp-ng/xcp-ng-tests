import pytest

@pytest.fixture(scope='package')
def host_no_sdn_controller(host):
    """ An XCP-ng with no SDN controller. """
    if host.xe('sdn-controller-list', minimal=True):
        pytest.skip(f"This test requires an XCP-ng with no SDN controller")
