
import pytest

import logging

from rpm_version import Evr  # type: ignore[import-untyped]

from lib.vm import VM

# Test that HVM VMs can be booted with XEN in both UEFI and BIOS modes with
# the maximum number of vCPUs supported by the guest OS, and that all vCPUs
# come up inside the guest.
#
# 128 is the maximum number of vCPUs supported by XCP-ng for HVM guests.
# Windows guests are limited to 64 vCPUs under XEN before 4.17.7-7.1.
#
# Requirements:
# - one XCP-ng host (--host) >= 8.3
# - a Linux or Windows VM (UEFI or BIOS) (--vm)

HVM_MAX_VCPUS = 128

# Before this Xen version, viridian_no_vp_limit is not enabled by default
# and Windows guests are limited to 64 vCPUs.
WINDOWS_VP_LIMIT = 64
XEN_NO_VP_LIMIT_VERSION = "4.17.7-7.1"


@pytest.mark.usefixtures("host_at_least_8_3")
@pytest.mark.multi_vms
class TestHvmVcpuLimit:
    @pytest.mark.parametrize('vm_with_vcpu_count', [HVM_MAX_VCPUS], indirect=True)
    def test_hvm_vcpu_limit(self, vm_with_vcpu_count: VM) -> None:
        vm = vm_with_vcpu_count
        vm.start()
        vm.wait_for_vm_running_and_ssh_up()
        try:
            xen_version = vm.host.get_installed_package_version('xen-hypervisor')
            assert xen_version is not None, "xen-hypervisor is not installed"
            if vm.is_windows and Evr.parse(xen_version) < Evr.parse(XEN_NO_VP_LIMIT_VERSION):
                expected = WINDOWS_VP_LIMIT
            else:
                expected = HVM_MAX_VCPUS
            actual = int(vm.ssh('nproc --all'))
            assert actual == expected, f"Expected {expected} vCPUs, got {actual}"
            logging.info("VM successfully booted in HVM %s mode with XEN and all %d vCPUs are up",
                         "UEFI" if vm.is_uefi else "BIOS", actual)
        finally:
            vm.shutdown(verify=True)
