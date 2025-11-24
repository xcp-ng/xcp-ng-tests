from __future__ import annotations

import pytest

import logging

from typing import TYPE_CHECKING, Generator

if TYPE_CHECKING:
    from lib.host import Host
    from lib.sr import SR

# NOTE: @pytest.mark.usefixtures does not parametrize this fixture.
# To recreate host_with_xfsprogs for each image_format value, accept
# image_format in the fixture arguments.
# ref https://docs.pytest.org/en/7.1.x/how-to/fixtures.html#use-fixtures-in-classes-and-modules-with-usefixtures
@pytest.fixture(scope='package')
def host_with_xfsprogs(host: Host, image_format: str):
    assert not host.file_exists('/usr/sbin/mkfs.xfs'), \
        "xfsprogs must not be installed on the host at the beginning of the tests"
    host.yum_save_state()
    host.yum_install(['xfsprogs'])
    yield host
    # teardown
    host.yum_restore_saved_state()

@pytest.fixture(scope='package')
def xfs_sr(unused_512B_disks: dict[Host, list[Host.BlockDeviceInfo]],
           host_with_xfsprogs: Host,
           image_format: str
           ) -> Generator[SR]:
    """ A XFS SR on first host. """
    sr_disk = unused_512B_disks[host_with_xfsprogs][0]["name"]
    sr = host_with_xfsprogs.sr_create('xfs', "XFS-local-SR-test",
                                      {'device': '/dev/' + sr_disk,
                                       'preferred-image-formats': image_format})
    yield sr
    # teardown
    sr.destroy()

@pytest.fixture(scope='module')
def vdi_on_xfs_sr(xfs_sr):
    vdi = xfs_sr.create_vdi('XFS-local-VDI-test')
    yield vdi
    vdi.destroy()

@pytest.fixture(scope='module')
def vm_on_xfs_sr(host, xfs_sr, vm_ref):
    vm = host.import_vm(vm_ref, sr_uuid=xfs_sr.uuid)
    yield vm
    # teardown
    logging.info("<< Destroy VM")
    vm.destroy(verify=True)
