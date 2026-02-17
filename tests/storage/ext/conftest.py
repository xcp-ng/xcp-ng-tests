from __future__ import annotations

import pytest

import logging

from lib.host import Host
from lib.sr import SR
from lib.vdi import VDI, ImageFormat
from lib.vm import VM

from typing import Any, Generator

@pytest.fixture(scope='package')
def ext_sr(host: Host,
           unused_512B_disks: dict[Host, list[Host.BlockDeviceInfo]],
           image_format: ImageFormat
           ) -> Generator[SR, None, None]:
    """ An EXT SR on first host. """
    sr_disk = unused_512B_disks[host][0]["name"]
    sr = host.sr_create('ext', "EXT-local-SR-test",
                        {'device': '/dev/' + sr_disk,
                         'preferred-image-formats': image_format})
    yield sr
    # teardown
    sr.destroy()

@pytest.fixture(scope='module')
def vdi_on_ext_sr(ext_sr: SR) -> Generator[VDI, None, None]:
    vdi = ext_sr.create_vdi('EXT-local-VDI-test')
    yield vdi
    vdi.destroy()

@pytest.fixture(scope='module')
def vm_on_ext_sr(host: Host, ext_sr: SR, vm_ref: str) -> Generator[VM, None, None]:
    vm = host.import_vm(vm_ref, sr_uuid=ext_sr.uuid)
    yield vm
    # teardown
    logging.info("<< Destroy VM")
    vm.destroy(verify=True)
