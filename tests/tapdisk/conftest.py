import pytest

@pytest.fixture(scope="module")
def vm_on_local_sr(host, local_sr_on_hostA1, vm_ref):
    vm = host.import_vm(vm_ref, local_sr_on_hostA1.uuid)
    vm.start()
    vm.wait_for_os_booted()
    yield vm
    vm.shutdown(force=True)
    vm.destroy(verify=True)
