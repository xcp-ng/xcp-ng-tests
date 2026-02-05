import pytest

import logging
import os

from pkgfixtures import host_with_saved_yum_state

# Requirements:
# - one XCP-ng host (--host) >= 8.3
# - the first network on the host can be used to reach the host

VCPUS = 128

@pytest.mark.multi_vms
@pytest.mark.usefixtures("host_at_least_8_3")
class TestVcpuLimit:
    def test_vcpus_limit(self, imported_vm):
        vm = imported_vm

        if (vm.is_running()):
            logging.info("VM already running, shutting it down first")
            vm.shutdown(verify=True)

        original_vcpus_max = vm.param_get('VCPUs-max')
        original_vcpus_at_startup = vm.param_get('VCPUs-at-startup')

        vm.param_set('VCPUs-max', str(VCPUS))
        vm.param_set('VCPUs-at-startup', str(VCPUS))
        vm.param_set("platform:vcpu-unrestricted", True)

        vm.start()
        vm.wait_for_os_booted()
        try:
            vcpu_count = vm.ssh("nproc --all")
            assert int(vcpu_count) == VCPUS, f"Unexpected vCPU count: {vcpu_count}"
        finally:
            vm.shutdown(verify=True)
            vm.param_set('VCPUs-at-startup', original_vcpus_at_startup)
            vm.param_set('VCPUs-max', original_vcpus_max)
