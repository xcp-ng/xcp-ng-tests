import pytest
from lib.common import wait_for, wait_for_not

pytestmark = pytest.mark.default_vm('mini-linux-x86_64-bios')

def test_basic_vm_crosspool_migrate(hosts, vm_ref):
    host1 = hosts[0]
    host2 = hosts[1]
    assert host1.pool_uuid() != host2.pool_uuid()

    vm = host1.import_vm_url(vm_ref)
    vm.start()
    vm.wait_for_vm_running_and_ssh_up(wait_for_ip=True)
    vm.migrate(host2)
    wait_for_not(vm.exists_on_previous_pool, "Wait for VM not on old pool anymore")
    wait_for(vm.exists, "Wait for VM on new pool")
    vm.wait_for_vm_running_and_ssh_up(wait_for_ip=True)
    vm.shutdown()
    wait_for(vm.is_halted, "Wait for VM halted")
    vm.destroy()
    wait_for_not(vm.exists, "Wait for VM destroyed")

