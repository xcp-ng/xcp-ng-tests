from __future__ import annotations

import pytest
from typing import TYPE_CHECKING, Generator

import logging

from lib.common import setup_formatted_and_mounted_disk, teardown_formatted_and_mounted_disk

if TYPE_CHECKING:
    from lib.common import DiskDevName
    from lib.host import Host
    from lib.pool import Pool

# Due to a bug in the way pytest handles the setup and teardown of package-scoped fixtures,
# we moved the following fixtures out of the main conftest.py.
# To workaround the bug, the fixture must be imported either in a package's own conftest.py,
# or directly in a test module. Then the fixtures will truly be handled as package-scoped.
# Reference: https://github.com/pytest-dev/pytest/issues/8189

# package scope because previous test packages may have used the disk
@pytest.fixture(scope='package')
def sr_disk_wiped(host: Host, sr_disk: DiskDevName) -> Generator[DiskDevName]:
    """A disk on MASTER HOST OF FIRST POOL which we wipe."""
    logging.info(">> wipe disk %s" % sr_disk)
    host.ssh(['wipefs', '-a', '/dev/' + sr_disk])
    yield sr_disk

# package scope so that the device is unmounted before tests from the next package is executed.
@pytest.fixture(scope='package')
def formatted_and_mounted_ext4_disk(host: Host, sr_disk: DiskDevName) -> Generator[str]:
    """Mountpoint for newly-formatted disk on MASTER HOST OF FIRST POOL."""
    mountpoint = '/var/tmp/sr_disk_mountpoint'
    setup_formatted_and_mounted_disk(host, sr_disk, 'ext4', mountpoint)
    yield mountpoint
    teardown_formatted_and_mounted_disk(host, mountpoint)

@pytest.fixture(scope='package')
def host_with_saved_yum_state(host: Host) -> Generator[Host]:
    host.yum_save_state()
    yield host
    host.yum_restore_saved_state()

@pytest.fixture(scope='package')
def pool_with_saved_yum_state(host: Host) -> Generator[Pool]:
    for h in host.pool.hosts:
        h.yum_save_state()
    yield host.pool
    host.pool.exec_on_hosts_on_error_continue(lambda h: h.yum_restore_saved_state())
