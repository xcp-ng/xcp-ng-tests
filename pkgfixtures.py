from __future__ import annotations

import pytest

import logging
from dataclasses import dataclass

from lib.common import setup_formatted_and_mounted_disk, teardown_formatted_and_mounted_disk
from lib.sr import SR
from lib.vdi import ImageFormat

from typing import TYPE_CHECKING, Generator

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
def sr_disk_wiped(host: Host, unused_512B_disks: dict[Host, list[Host.BlockDeviceInfo]]) -> Generator[DiskDevName]:
    """A disk on MASTER HOST OF FIRST POOL which we wipe."""
    host_disks = unused_512B_disks[host]
    assert host_disks, f"No 512B disk available on host {host}"
    sr_disk = host_disks[0].name
    logging.info(">> wipe disk %s" % sr_disk)
    host.ssh(f'wipefs -a /dev/{sr_disk}')
    yield sr_disk

# package scope so that the device is unmounted before tests from the next package is executed.
@pytest.fixture(scope='package')
def formatted_and_mounted_ext4_disk(host: Host, unused_512B_disks: dict[Host, list[Host.BlockDeviceInfo]]
                                    ) -> Generator[str]:
    """Mountpoint for newly-formatted disk on MASTER HOST OF FIRST POOL."""
    mountpoint = '/var/tmp/sr_disk_mountpoint'
    host_disks = unused_512B_disks[host]
    assert host_disks, f"No 512B disk available on host {host}"
    sr_disk = host_disks[0].name
    setup_formatted_and_mounted_disk(host, sr_disk, 'ext4', mountpoint)
    yield mountpoint
    teardown_formatted_and_mounted_disk(host, mountpoint)

def _host_with_saved_yum_state(host: Host, restart_toolstack: bool) -> Generator[Host]:
    """
    Saves the yum state and restores the saved state on teardown.

    It also optionally restarts the toolstack.
    Fixtures using this function should not be used concurrently in the same test run.
    """
    host.yum_save_state()
    yield host
    host.yum_restore_saved_state()
    if restart_toolstack:
        host.restart_toolstack(verify=True)

@pytest.fixture(scope='package')
def host_with_saved_yum_state(host: Host) -> Generator[Host]:
    """
    Saves the yum state and then restore it on teardown.

    Should not be used concurrently with another "host_with_saved_yum_state" fixture
    """
    yield from _host_with_saved_yum_state(host, False)

@pytest.fixture(scope='package')
def host_with_saved_yum_state_toolstack_restart(host: Host) -> Generator[Host]:
    """
    Saves the yum state then restore it and restarts the toolstack on teardown.

    Should not be used concurrently with another "host_with_saved_yum_state" fixture
    """
    yield from _host_with_saved_yum_state(host, True)

@pytest.fixture(scope='package')
def pool_with_saved_yum_state(host: Host) -> Generator[Pool]:
    for h in host.pool.hosts:
        h.yum_save_state()
    yield host.pool
    host.pool.exec_on_hosts_on_error_continue(lambda h: h.yum_restore_saved_state())


@dataclass
class XfsConfig:
    uninstall_xfs: bool = True

@pytest.fixture(scope='package')
def _xfs_config_on_hostA2() -> XfsConfig:
    return XfsConfig()

# NOTE: @pytest.mark.usefixtures does not parametrize this fixture.
# To recreate host_with_xfsprogs for each image_format value, accept
# image_format in the fixture arguments.
# ref https://docs.pytest.org/en/7.1.x/how-to/fixtures.html#use-fixtures-in-classes-and-modules-with-usefixtures
@pytest.fixture(scope='package')
def hostA2_with_xfsprogs(hostA2: Host, image_format: ImageFormat, _xfs_config_on_hostA2: XfsConfig) \
        -> Generator[Host, None, None]:
    assert not hostA2.file_exists('/usr/sbin/mkfs.xfs'), \
        "xfsprogs must not be installed on the host at the beginning of the tests"
    hostA2.yum_save_state()
    hostA2.yum_install(['xfsprogs'])
    yield hostA2
    # teardown
    if _xfs_config_on_hostA2.uninstall_xfs:
        hostA2.yum_restore_saved_state()

@pytest.fixture(scope='package')
def xfs_sr_on_hostA2(
    unused_512B_disks: dict[Host, list[Host.BlockDeviceInfo]],
    hostA2_with_xfsprogs: Host,
    image_format: ImageFormat,
    _xfs_config_on_hostA2: XfsConfig,
) -> Generator[SR, None, None]:
    """ A XFS SR on first host. """
    sr_disk = unused_512B_disks[hostA2_with_xfsprogs][0].name
    sr = hostA2_with_xfsprogs.sr_create('xfs', "XFS-local-SR-test",
                                        {'device': '/dev/' + sr_disk,
                                         'preferred-image-formats': image_format})
    yield sr
    # teardown
    try:
        sr.destroy()
    except Exception as e:
        _xfs_config_on_hostA2.uninstall_xfs = False
        raise pytest.fail("Could not destroy xfs SR, leaving packages in place for manual cleanup") from e

@pytest.fixture(scope='package')
def _xfs_config_on_hostB1() -> XfsConfig:
    return XfsConfig()

# NOTE: @pytest.mark.usefixtures does not parametrize this fixture.
# To recreate host_with_xfsprogs for each image_format value, accept
# image_format in the fixture arguments.
# ref https://docs.pytest.org/en/7.1.x/how-to/fixtures.html#use-fixtures-in-classes-and-modules-with-usefixtures
@pytest.fixture(scope='package')
def hostB1_with_xfsprogs(hostB1: Host, image_format: ImageFormat, _xfs_config_on_hostB1: XfsConfig) \
        -> Generator[Host, None, None]:
    assert not hostB1.file_exists('/usr/sbin/mkfs.xfs'), \
        "xfsprogs must not be installed on the host at the beginning of the tests"
    hostB1.yum_save_state()
    hostB1.yum_install(['xfsprogs'])
    yield hostB1
    # teardown
    if _xfs_config_on_hostB1.uninstall_xfs:
        hostB1.yum_restore_saved_state()

@pytest.fixture(scope='package')
def xfs_sr_on_hostB1(
    unused_512B_disks: dict[Host, list[Host.BlockDeviceInfo]],
    hostB1_with_xfsprogs: Host,
    image_format: ImageFormat,
    _xfs_config_on_hostB1: XfsConfig,
) -> Generator[SR, None, None]:
    """ A XFS SR on first host. """
    sr_disk = unused_512B_disks[hostB1_with_xfsprogs][0].name
    sr = hostB1_with_xfsprogs.sr_create('xfs', "XFS-local-SR-test",
                                        {'device': '/dev/' + sr_disk,
                                         'preferred-image-formats': image_format})
    yield sr
    # teardown
    try:
        sr.destroy()
    except Exception as e:
        _xfs_config_on_hostB1.uninstall_xfs = False
        raise pytest.fail("Could not destroy xfs SR, leaving packages in place for manual cleanup") from e
