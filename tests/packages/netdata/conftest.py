import pytest

from lib.host import Host

# Explicitly import package-scoped fixtures (see explanation in pkgfixtures.py)
from pkgfixtures import host_with_saved_yum_state

from typing import Generator

@pytest.fixture(scope="package")
def host_with_netdata(host_with_saved_yum_state: Host) -> Generator[Host, None, None]:
    host = host_with_saved_yum_state
    # Installing netdata-ui also installs netdata and all required dependencies
    host.yum_install(['netdata-ui'])
    yield host
