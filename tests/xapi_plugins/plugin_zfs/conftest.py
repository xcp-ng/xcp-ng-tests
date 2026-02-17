import pytest

from lib.host import Host

# Explicitly import package-scoped fixtures (see explanation in pkgfixtures.py)
from pkgfixtures import host_with_saved_yum_state, sr_disk_wiped

from typing import Generator

@pytest.fixture(scope='package')
def host_without_zfs(host: Host) -> Generator[None, None, None]:
    assert not host.file_exists('/usr/sbin/zpool'), \
        "zfs must not be installed on the host at the beginning of the tests"
    yield

@pytest.fixture(scope='package')
def host_with_zfs(host_without_zfs: Host, host_with_saved_yum_state: Host) -> Generator[Host, None, None]:
    host = host_with_saved_yum_state
    host.yum_install(['zfs'])
    host.ssh('modprobe zfs')
    yield host

@pytest.fixture(scope='package')
def zpool_vol0(sr_disk_wiped: str, host_with_zfs: Host) -> Generator[None, None, None]:
    host_with_zfs.ssh(f'zpool create -f vol0 /dev/{sr_disk_wiped}')
    yield
    # teardown
    host_with_zfs.ssh('zpool destroy vol0')
