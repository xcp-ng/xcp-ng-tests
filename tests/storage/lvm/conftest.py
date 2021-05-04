import pytest

@pytest.fixture(scope='session')
def lvm_sr(host, sr_disk):
    """ An LVM SR on first host. """
    sr = host.sr_create('lvm', "LVM-local-SR", {'device': '/dev/' + sr_disk})
    yield sr
    # teardown
    sr.destroy()

@pytest.fixture(scope='module')
def vm_on_lvm_sr(host, lvm_sr, vm_ref):
    print(">> ", end='')
    vm = host.import_vm(vm_ref, sr_uuid=lvm_sr.uuid)
    yield vm
    # teardown
    print("<< Destroy VM")
    vm.destroy(verify=True)
