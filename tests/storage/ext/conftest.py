from __future__ import annotations

import pytest

import logging

from lib.host import Host
from lib.sr import SR

from typing import Generator

@pytest.fixture(scope='package')
def ext_sr(host: Host, unused_512B_disks: dict[Host, list[Host.BlockDeviceInfo]]) -> Generator[SR]:
    """ An EXT SR on first host. """
    sr_disk = unused_512B_disks[host][0]["name"]
    sr = host.sr_create('ext', "EXT-local-SR-test", {'device': '/dev/' + sr_disk})
    yield sr
    # teardown
    sr.destroy()

@pytest.fixture(scope='module')
def vdi_on_ext_sr(ext_sr: SR):
    vdi = ext_sr.create_vdi('EXT-local-VDI-test')
    yield vdi
    vdi.destroy()

@pytest.fixture(scope='module')
def vm_on_ext_sr(host, ext_sr, vm_ref):
    vm = host.import_vm(vm_ref, sr_uuid=ext_sr.uuid)
    yield vm
    # teardown
    logging.info("<< Destroy VM")
    vm.destroy(verify=True)
