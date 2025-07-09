import pytest

import logging

@pytest.fixture(scope='package')
def largeblock_sr(host, sr_disk_4k):
    """ A LARGEBLOCK SR on first host. """
    sr = host.sr_create('largeblock', "LARGEBLOCK-local-SR-test", {'device': '/dev/' + sr_disk_4k})
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
