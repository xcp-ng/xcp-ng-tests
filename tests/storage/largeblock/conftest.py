from __future__ import annotations

import pytest

import logging

from typing import TYPE_CHECKING, Generator

if TYPE_CHECKING:
    from lib.host import Host
    from lib.sr import SR

@pytest.fixture(scope='package')
def largeblock_sr(host: Host, unused_4k_disks: dict[Host, list[Host.BlockDeviceInfo]]) -> Generator[SR]:
    """ A LARGEBLOCK SR on first host. """
    sr_disk = unused_4k_disks[host][0]["name"]
    sr = host.sr_create('largeblock', "LARGEBLOCK-local-SR-test", {'device': '/dev/' + sr_disk})
    yield sr
    # teardown
    sr.destroy()

@pytest.fixture(scope='module')
def vdi_on_largeblock_sr(largeblock_sr):
    vdi = largeblock_sr.create_vdi('LARGEBLOCK-local-VDI-test')
    yield vdi
    vdi.destroy()

@pytest.fixture(scope='module')
def vm_on_largeblock_sr(host, largeblock_sr, vm_ref):
    vm = host.import_vm(vm_ref, sr_uuid=largeblock_sr.uuid)
    yield vm
    # teardown
    logging.info("<< Destroy VM")
    vm.destroy(verify=True)
