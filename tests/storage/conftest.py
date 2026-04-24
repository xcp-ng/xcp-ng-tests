from __future__ import annotations

import pytest

import logging
from dataclasses import dataclass

from lib import config
from lib.common import randid
from lib.host import Host
from lib.sr import SR
from lib.vdi import ImageFormat
from lib.vm import VM
from tests.storage import install_randstream

from typing import Any, Generator

def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    # modify ordering so that ext is always tested first,
    # before more complex storage drivers
    for item in reversed(list(items)):
        if "_ext_" in item.path.name:
            items.remove(item)
            items.insert(0, item)

@pytest.fixture(scope='module')
def storage_test_vm(running_unix_vm: VM) -> Generator[VM, None, None]:
    install_randstream(running_unix_vm)
    yield running_unix_vm

@pytest.fixture()
def temp_large_dir(host: Host) -> Generator[str, None, None]:
    """Create a temporary directory on a large (NFS) disk on the primary host"""
    temp_id = randid()
    nfs_conf = config.sr_device_config("NFS_DEVICE_CONFIG")
    if 'serverpath' in nfs_conf or 'server' in nfs_conf:
        mount_path = f'/mnt/nfs_{temp_id}'
        host.ssh(f'mkdir -p {mount_path}')
        path = f'{mount_path}/temp_large_dir_{temp_id}'
        host.ssh(f'mount -t nfs {nfs_conf["server"]}:{nfs_conf["serverpath"]} {mount_path}')
        host.ssh(f'mkdir {path}')
        yield path
        host.ssh(f'rm -rf {path}')
        host.ssh(f'umount {mount_path}')
        host.ssh(f'rmdir {mount_path}')
    else:
        logging.warning("No NFS configuration found, using /tmp")
        path = f'/tmp/temp_large_dir_{temp_id}'
        host.ssh(f'mkdir {path}')
        yield path
        host.ssh(f'rm -rf {path}')


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
    sr_disk = unused_512B_disks[hostA2_with_xfsprogs][0]["name"]
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
    sr_disk = unused_512B_disks[hostB1_with_xfsprogs][0]["name"]
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
