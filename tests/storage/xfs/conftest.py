from __future__ import annotations

import pytest

import logging
from dataclasses import dataclass

from lib.host import Host
from lib.sr import SR
from lib.vdi import VDI, ImageFormat
from lib.vm import VM

from typing import Generator

@dataclass
class XfsConfig:
    uninstall_xfs: bool = True

@pytest.fixture(scope='package')
def _xfs_config() -> XfsConfig:
    return XfsConfig()

# NOTE: @pytest.mark.usefixtures does not parametrize this fixture.
# To recreate host_with_xfsprogs for each image_format value, accept
# image_format in the fixture arguments.
# ref https://docs.pytest.org/en/7.1.x/how-to/fixtures.html#use-fixtures-in-classes-and-modules-with-usefixtures
@pytest.fixture(scope='package')
def host_with_xfsprogs(host: Host, image_format: ImageFormat, _xfs_config: XfsConfig) -> Generator[Host]:
    assert not host.file_exists('/usr/sbin/mkfs.xfs'), \
        "xfsprogs must not be installed on the host at the beginning of the tests"
    host.yum_save_state()
    host.yum_install(['xfsprogs'])
    yield host
    # teardown
    if _xfs_config.uninstall_xfs:
        host.yum_restore_saved_state()

@pytest.fixture(scope='package')
def xfs_sr(
    unused_512B_disks: dict[Host, list[Host.BlockDeviceInfo]],
    host_with_xfsprogs: Host,
    image_format: ImageFormat,
    _xfs_config: XfsConfig,
) -> Generator[SR]:
    """ A XFS SR on first host. """
    sr_disk = unused_512B_disks[host_with_xfsprogs][0]["name"]
    sr = host_with_xfsprogs.sr_create('xfs', "XFS-local-SR-test",
                                      {'device': '/dev/' + sr_disk,
                                       'preferred-image-formats': image_format})
    yield sr
    # teardown
    try:
        sr.destroy()
    except Exception as e:
        _xfs_config.uninstall_xfs = False
        raise pytest.fail("Could not destroy xfs SR, leaving packages in place for manual cleanup") from e

@pytest.fixture(scope='module')
def vdi_on_xfs_sr(xfs_sr: SR) -> Generator[VDI]:
    vdi = xfs_sr.create_vdi('XFS-local-VDI-test')
    yield vdi
    vdi.destroy()

@pytest.fixture(scope='module')
def vm_on_xfs_sr(host: Host, xfs_sr: SR, vm_ref: str) -> Generator[VM]:
    vm = host.import_vm(vm_ref, sr_uuid=xfs_sr.uuid)
    yield vm
    # teardown
    logging.info("<< Destroy VM")
    vm.destroy(verify=True)
