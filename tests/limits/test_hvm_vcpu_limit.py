import pytest

import logging

# Test that HVM VMs can be booted with XEN in both UEFI and BIOS modes with
# 128 vCPUs, and that all 128 vCPUs come up inside the guest.
#
# 128 is the maximum number of vCPUs supported by XCP-ng for HVM guests.
#
# Requirements:
# - one XCP-ng host (--host) >= 8.3
# - a Linux or Windows VM (UEFI or BIOS) (--vm)

HVM_MAX_VCPUS = 128


@pytest.mark.multi_vms
class TestHvmVcpuLimit:
    @pytest.mark.parametrize('vm_with_vcpu_count', [HVM_MAX_VCPUS], indirect=True)
    def test_hvm_vcpu_limit(self, vm_with_vcpu_count):
        vm = vm_with_vcpu_count
        vm.start()
        vm.wait_for_vm_running_and_ssh_up()
        try:
            actual = int(vm.ssh(['nproc', '--all']))
            assert actual == HVM_MAX_VCPUS, f"Expected {HVM_MAX_VCPUS} vCPUs, got {actual}"
            logging.info("VM successfully booted in HVM %s mode with XEN and all %d vCPUs are up",
                         "UEFI" if vm.is_uefi else "BIOS", actual)
        finally:
            vm.shutdown(verify=True)
