import pytest

import logging

from lib import config
from lib.host import Host
from lib.sr import SR
from lib.vdi import VDI
from lib.vm import VM

from typing import Generator

@pytest.fixture(scope='package')
def lvmoiscsi_device_config() -> dict[str, str]:
    return config.sr_device_config("LVMOISCSI_DEVICE_CONFIG")

@pytest.fixture(scope='package')
def lvmoiscsi_sr(host: Host, lvmoiscsi_device_config: dict[str, str]) -> Generator[SR, None, None]:
    """ A lvmoiscsi SR on first host. """
    sr = host.sr_create('lvmoiscsi', "lvmoiscsi-SR-test", lvmoiscsi_device_config, shared=True)
    yield sr
    # teardown
    sr.destroy()

@pytest.fixture(scope='module')
def vdi_on_lvmoiscsi_sr(lvmoiscsi_sr: SR) -> Generator[VDI, None, None]:
    vdi = lvmoiscsi_sr.create_vdi('lvmoiscsi-VDI-test')
    yield vdi
    vdi.destroy()

@pytest.fixture(scope='module')
def vm_on_lvmoiscsi_sr(host: Host, lvmoiscsi_sr: SR, vm_ref: str) -> Generator[VM, None, None]:
    vm = host.import_vm(vm_ref, sr_uuid=lvmoiscsi_sr.uuid)
    yield vm
    # teardown
    logging.info("<< Destroy VM")
    vm.destroy(verify=True)
