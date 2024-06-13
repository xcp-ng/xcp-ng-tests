import pytest

@pytest.fixture(scope="package")
def host_with_netdata(host_with_saved_yum_state):
    host = host_with_saved_yum_state
    # Installing netdata-ui also installs netdata and all required dependencies
    host.yum_install(['netdata-ui'])
    yield host
