from __future__ import annotations

import pytest

import logging

from lib import config
from lib.host import Host
from lib.sr import SR
from lib.vdi import QCOW2_IMAGE_FORMAT, VDI, ImageFormat
from lib.vm import VM

# explicit import for package-scope fixtures
from pkgfixtures import (
    _xfs_config_on_hostA2,
    _xfs_config_on_hostB1,
    hostA2_with_xfsprogs,
    hostB1_with_xfsprogs,
    xfs_sr_on_hostA2,
    xfs_sr_on_hostB1,
)

from typing import Any, Generator

@pytest.fixture(scope='package')
def ext_sr(host: Host,
           unused_512B_disks: dict[Host, list[Host.BlockDeviceInfo]],
           image_format: ImageFormat
           ) -> Generator[SR, None, None]:
    """ An EXT SR on first host. """
    sr_disk = unused_512B_disks[host][0].name
    sr = host.sr_create('ext', "EXT-local-SR-test",
                        {'device': '/dev/' + sr_disk,
                         'preferred-image-formats': image_format})
    yield sr
    # teardown
    sr.destroy()

@pytest.fixture(scope='module')
def vdi_on_ext_sr(ext_sr: SR) -> Generator[VDI, None, None]:
    vdi = ext_sr.create_vdi('EXT-local-VDI-test', virtual_size=config.volume_size)
    yield vdi
    vdi.destroy()

@pytest.fixture(scope='module')
def vm_on_ext_sr(host: Host, ext_sr: SR, vm_ref: str) -> Generator[VM, None, None]:
    vm = host.import_vm(vm_ref, sr_uuid=ext_sr.uuid)
    yield vm
    # teardown
    logging.info("<< Destroy VM")
    vm.destroy(verify=True)

@pytest.fixture(scope='package')
def ext_sr_4k(host: Host,
              unused_4k_disks: dict[Host, list[Host.BlockDeviceInfo]]) -> Generator[SR, None, None]:
    """An EXT SR on a native 4KiB block device, using the QCOW2 image format."""
    sr_disk = unused_4k_disks[host][0].name
    sr = host.sr_create('ext', "EXT-4K-local-SR-test",
                        {'device': '/dev/' + sr_disk,
                         'preferred-image-formats': QCOW2_IMAGE_FORMAT})
    yield sr
    # teardown
    sr.destroy()

@pytest.fixture(scope='module')
def vdi_on_ext_sr_4k(ext_sr_4k: SR) -> Generator[VDI, None, None]:
    vdi = ext_sr_4k.create_vdi('EXT-4K-local-VDI-test', virtual_size=config.volume_size)
    yield vdi
    vdi.destroy()

@pytest.fixture(scope='module')
def vm_on_ext_sr_4k(host: Host, ext_sr_4k: SR, vm_ref: str) -> Generator[VM, None, None]:
    vm = host.import_vm(vm_ref, sr_uuid=ext_sr_4k.uuid)
    yield vm
    # teardown
    logging.info("<< Destroy VM")
    vm.destroy(verify=True)
