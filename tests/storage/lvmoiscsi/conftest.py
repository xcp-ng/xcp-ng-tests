import pytest

import logging

from lib import config

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.sr import SR

@pytest.fixture(scope='package')
def lvmoiscsi_device_config():
    return config.sr_device_config("LVMOISCSI_DEVICE_CONFIG")

@pytest.fixture(scope='package')
def lvmoiscsi_sr(host, lvmoiscsi_device_config):
    """ A lvmoiscsi SR on first host. """
    sr = host.sr_create('lvmoiscsi', "lvmoiscsi-SR-test", lvmoiscsi_device_config, shared=True)
    yield sr
    # teardown
    sr.destroy()

@pytest.fixture(scope='module')
def vdi_on_lvmoiscsi_sr(lvmoiscsi_sr: 'SR'):
    vdi = lvmoiscsi_sr.create_vdi('lvmoiscsi-VDI-test', virtual_size='1GiB')
    yield vdi
    vdi.destroy()

@pytest.fixture(scope='module')
def vm_on_lvmoiscsi_sr(host, lvmoiscsi_sr, vm_ref):
    vm = host.import_vm(vm_ref, sr_uuid=lvmoiscsi_sr.uuid)
    yield vm
    # teardown
    logging.info("<< Destroy VM")
    vm.destroy(verify=True)
