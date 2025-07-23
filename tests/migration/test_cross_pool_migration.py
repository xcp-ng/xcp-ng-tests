import pytest

import logging

from lib.common import wait_for, wait_for_not

@pytest.mark.multi_vms # run on a variety of VMs
@pytest.mark.big_vm # and also on a really big VM ideally
def test_cross_pool_migration(hostB1, imported_vm):
    vm = imported_vm.clone()
    try:
        vm.start()
        vm.wait_for_os_booted()
        vm.migrate(hostB1)
        wait_for_not(vm.exists_on_previous_pool, "Wait for VM not on old pool anymore")
        wait_for(vm.exists, "Wait for VM on new pool")
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)
    finally:
        logging.info("Destroy VM %s" % vm.uuid)
        vm.destroy()
