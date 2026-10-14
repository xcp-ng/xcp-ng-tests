import pytest

import logging

from lib.common import Defer, wait_for, wait_for_not
from lib.host import Host
from lib.tracing import Tracing
from lib.vm import VM

@pytest.mark.multi_vms # run on a variety of VMs
@pytest.mark.big_vm # and also on a really big VM ideally
def test_cross_pool_migration(hostB1: Host, imported_vm: VM, defer: Defer, tracing: Tracing) -> None:
    vm = imported_vm.clone()
    defer(lambda: vm.destroy())
    vm.start()
    vm.wait_for_os_booted()
    traceparent = tracing.gen_traceparent()
    vm.migrate(hostB1, vars={'TRACEPARENT': traceparent})
    wait_for_not(vm.exists_on_previous_pool, "Wait for VM not on old pool anymore")
    wait_for(vm.exists, "Wait for VM on new pool")
    vm.wait_for_os_booted()
    vm.shutdown(verify=True)
    tracing.stats_migration(traceparent)
