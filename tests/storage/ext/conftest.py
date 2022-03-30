import logging
import pytest

@pytest.fixture(scope='session')
def ext_sr(host, sr_disk):
    """ An EXT SR on first host. """
    sr = host.sr_create('ext', "EXT-local-SR-test", {'device': '/dev/' + sr_disk})
    yield sr
    # teardown
    sr.destroy()

@pytest.fixture(scope='module')
def vm_on_ext_sr(host, ext_sr, vm_ref):
    vm = host.import_vm(vm_ref, sr_uuid=ext_sr.uuid)
    yield vm
    # teardown
    logging.info("<< Destroy VM")
    vm.destroy(verify=True)
