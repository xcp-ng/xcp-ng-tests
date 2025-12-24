from __future__ import annotations

import pytest

import logging

from lib.sr import SR

# Explicitly import package-scoped fixtures (see explanation in pkgfixtures.py)
from lib.vdi import ImageFormat
from pkgfixtures import host_with_saved_yum_state, sr_disk_wiped

from typing import TYPE_CHECKING, Generator

if TYPE_CHECKING:
    from lib.host import Host
    from lib.sr import SR

POOL_NAME = 'pool0'
POOL_PATH = '/' + POOL_NAME

@pytest.fixture(scope='package')
def host_without_zfs(host):
    assert not host.file_exists('/usr/sbin/zpool'), \
        "zfs must not be installed on the host at the beginning of the tests"

# NOTE: @pytest.mark.usefixtures does not parametrize this fixture.
# To recreate host_with_zfs for each image_format value, accept
# image_format in the fixture arguments.
# ref https://docs.pytest.org/en/7.1.x/how-to/fixtures.html#use-fixtures-in-classes-and-modules-with-usefixtures
@pytest.fixture(scope='package')
def host_with_zfs(host_without_zfs: Host,
                  host_with_saved_yum_state: Host,
                  image_format: ImageFormat
                  ) -> Generator[Host]:
    host = host_with_saved_yum_state
    host.yum_install(['zfs'])
    host.ssh(['modprobe', 'zfs'])
    yield host

@pytest.fixture(scope='package')
def zpool_vol0(sr_disk_wiped, host_with_zfs):
    host_with_zfs.ssh(['zpool', 'create', '-f', POOL_NAME, '/dev/' + sr_disk_wiped])
    yield
    # teardown
    host_with_zfs.ssh(['zpool', 'destroy', POOL_NAME])

@pytest.fixture(scope='package')
def zfs_sr(host: Host, image_format: ImageFormat, zpool_vol0: None) -> Generator[SR]:
    """ A ZFS SR on first host. """
    sr = host.sr_create('zfs', "ZFS-local-SR-test", {
        'location': POOL_PATH,
        'preferred-image-formats': image_format
    }, verify=True)
    yield sr
    # teardown
    sr.destroy()

@pytest.fixture(scope='module')
def vdi_on_zfs_sr(zfs_sr: SR):
    vdi = zfs_sr.create_vdi('ZFS-local-VDI-test')
    yield vdi
    vdi.destroy()

@pytest.fixture(scope='module')
def vm_on_zfs_sr(host, zfs_sr, vm_ref):
    vm = host.import_vm(vm_ref, sr_uuid=zfs_sr.uuid)
    yield vm
    # teardown
    logging.info("<< Destroy VM")
    vm.destroy(verify=True)
