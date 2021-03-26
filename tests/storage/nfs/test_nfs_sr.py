import pytest
from lib.common import wait_for, vm_image
import time
import subprocess

# Requirements:
# - one XCP-ng host >= 8.0 with an additional unused disk for the SR

class TestNFSSRCreateDestroy:
    def test_create_and_destroy_sr(self, host, sr_disk, nfs_device_config):
        # Create and destroy tested in the same test to leave the host as unchanged as possible
        sr = host.sr_create('nfs', "NFS-SR", nfs_device_config, shared=True, verify=True)
        # import a VM in order to detect vm import issues here rather than in the vm_on_nfs fixture used in
        # the next tests, because errors in fixtures break teardown
        vm = host.import_vm(vm_image('mini-linux-x86_64-bios'), sr_uuid=sr.uuid)
        vm.destroy(verify=True)
        sr.destroy(verify=True)

@pytest.mark.usefixtures("nfs_sr", "vm_on_nfs_sr")
class TestNFSSR:
    def test_start_and_shutdown_VM(self, vm_on_nfs_sr):
        vm = vm_on_nfs_sr
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    def test_snapshot(self, vm_on_nfs_sr):
        vm = vm_on_nfs_sr
        vm.start()
        vm.wait_for_os_booted()
        vm.test_snapshot_on_running_vm()
        vm.shutdown(verify=True)

    # *** tests with reboots (longer tests).

    def test_reboot(self, host, nfs_sr, vm_on_nfs_sr):
        sr = nfs_sr
        vm = vm_on_nfs_sr
        host.reboot(verify=True)
        wait_for(sr.all_pbds_attached, "Wait for PDB attached")
        # start the VM as a way to check that the underlying SR is operational
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    # *** End of tests with reboots
