import logging
import os
import pytest
import tempfile

# Requirements:
# - an XCP-ng host (--hosts) >= 8.2
# - a VM (--vm)
# - enough space to import 4 VMs on default SR
# - the pool must have 1 shared SR
# - each host must have a local SR

@pytest.fixture(scope='module')
def four_vms(imported_vm):
    vm1 = imported_vm
    vm2 = vm1.clone()
    vm3 = vm1.clone()
    vm4 = vm1.clone()
    yield (vm1, vm2, vm3, vm4)
    # teardown
    logging.info("< Destroy VM4")
    vm4.destroy()
    logging.info("< Destroy VM3")
    vm3.destroy()
    logging.info("< Destroy VM2")
    vm2.destroy()


class TestIrqBalance:
    def test_start_four_vms(self, four_vms):
        for vm in four_vms:
            vm.start()

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
                output = vm.host.ssh([rf'grep /proc/interrupts -e "xen-dyn\\s\+-event\\s\+{device_id}-"'])
                assert len(output) > 0
                for line in output.splitlines():
                    fields = line.split()
                    irqs_per_cpu = fields[1:fields.index('xen-dyn')]
                    for i, val in enumerate(irqs_per_cpu):
                        if int(val) > 0:
                            logging.info(f"VIF {device_id}: {val} IRQs for CPU {i}")
                            cpus.add(i)

        assert len(cpus) > 1, "there must be more than one CPU that handles the IRQs"
