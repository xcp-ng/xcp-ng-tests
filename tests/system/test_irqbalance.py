import pytest

import logging
import os
import tempfile

from lib.common import exec_nofail, raise_errors

# Requirements:
# - an XCP-ng host (--hosts) >= 8.2
# - a VM (--vm)
# - enough space to import 4 VMs on default SR
# - the default SR must be either shared or local on master host, so that VMs can all start on the same host

@pytest.fixture(scope='module')
def four_vms(imported_vm):
    vm1 = imported_vm
    vm2 = vm1.clone()
    vm3 = vm1.clone()
    vm4 = vm1.clone()
    yield (vm1, vm2, vm3, vm4)
    # teardown
    errors = []
    logging.info("< Destroy VM4")
    errors += exec_nofail(lambda: vm4.destroy())
    logging.info("< Destroy VM3")
    errors += exec_nofail(lambda: vm3.destroy())
    logging.info("< Destroy VM2")
    errors += exec_nofail(lambda: vm2.destroy())
    raise_errors(errors)

@pytest.mark.flaky # sometimes IRQs are not balanced and we don't know why. And sometimes a VM doesn't report an IP.
@pytest.mark.small_vm
class TestIrqBalance:
    """
    In the past, a security fix broke IRQ balancing for VIFs.
    We want to avoid this to happen again, so this testcase runs several VMs
    and verifies that the IRQs are balanced on more than one CPU.
    """
    def test_start_four_vms(self, host, four_vms):
        for vm in four_vms:
            vm.start(on=host.uuid)

        for vm in four_vms:
            vm.wait_for_vm_running_and_ssh_up()

        logging.info("Create some network traffic for each VM")
        with tempfile.NamedTemporaryFile() as f:
            f.write(os.urandom(2000000))
            for vm in four_vms:
                vm.scp(f.name, f.name)
                vm.ssh(['rm', '-f', f.name])

        logging.info("Check that the IRQs of the VMs VIFs are not all on the same CPU on dom0")
        cpus = set()
        for vm in four_vms:
            # List the CPU(s) that handled IRQs for the VM's vifs
            for vif in vm.vifs():
                device_id = vif.device_id()
                # depending on kernel patches, we're looking either for xen-dyn or xen-dyn-lateeoi
                output = vm.host.ssh([rf'grep /proc/interrupts -e "xen-dyn\(-lateeoi\)\?\\s\+-event\\s\+{device_id}-"'])
                assert len(output) > 0
                for line in output.splitlines():
                    fields = line.split()
                    try:
                        xen_dyn_index = fields.index('xen-dyn')
                    except ValueError:
                        xen_dyn_index = fields.index('xen-dyn-lateeoi')
                    irqs_per_cpu = fields[1:xen_dyn_index]
                    for i, val in enumerate(irqs_per_cpu):
                        if int(val) > 0:
                            logging.info(f"VIF {device_id}: {val} IRQs for CPU {i}")
                            cpus.add(i)

        assert len(cpus) > 1, "there must be more than one CPU that handles the IRQs"
