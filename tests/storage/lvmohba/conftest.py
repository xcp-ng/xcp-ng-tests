import pytest

import logging

from lib import config
from lib.sr import SR
from lib.vdi import ImageFormat

@pytest.fixture(scope='package')
def lvmohba_device_config():
    return config.sr_device_config("LVMOHBA_DEVICE_CONFIG")

@pytest.fixture(scope='package')
def lvmohba_sr(host, lvmohba_device_config, image_format: ImageFormat):
    """ A lvmohba SR on first host. """
    sr = host.sr_create('lvmohba', "lvmohba-SR-test",
                        lvmohba_device_config | {'preferred-image-formats': image_format}, shared=True)
    yield sr
    # teardown
    sr.destroy()

@pytest.fixture()
def vdi_on_lvmohba_sr(lvmohba_sr: SR):
    vdi = lvmohba_sr.create_vdi('lvmohba-VDI-test', virtual_size=config.volume_size)
    yield vdi
    vdi.destroy()

@pytest.fixture(scope='module')
def vm_on_lvmohba_sr(host, lvmohba_sr, vm_ref):
    vm = host.import_vm(vm_ref, sr_uuid=lvmohba_sr.uuid)
    yield vm
    # teardown
    logging.info("<< Destroy VM")
    vm.destroy(verify=True)
