import pytest
import subprocess
from lib.common import wait_for, vm_image

# Requirements:
# - one XCP-ng host with an additional unused disk for the SR

class TestLVMSRCreateDestroy:
    """
    Tests that do not use fixtures that setup the SR or import VMs,
    because they precisely need to test SR creation and destruction,
    and VM import.
    """

    def test_create_sr_with_device_missing(self, host):
        try:
            sr = host.sr_create('ext', 'LVM-local-SR', {}, verify=True)
        except subprocess.CalledProcessError as e:
            assert e.stdout == (
                b'Error code: SR_BACKEND_FAILURE_90\nError parameters: , '
                b'The request is missing the device parameter, \n'
            ), 'Bad error, current: {}'.format(e.stdout)
            return
        assert False, 'SR creation should not have succeeded!'

    def test_create_and_destroy_sr(self, host, sr_disk):
        # Create and destroy tested in the same test to leave the host as unchanged as possible
        sr = host.sr_create('lvm', "LVM-local-SR", {'device': '/dev/' + sr_disk}, verify=True)
        # import a VM in order to detect vm import issues here rather than in the vm_on_xfs_fixture used in
        # the next tests, because errors in fixtures break teardown
        vm = host.import_vm(vm_image('mini-linux-x86_64-bios'), sr_uuid=sr.uuid)
        vm.destroy(verify=True)
        sr.destroy(verify=True)


@pytest.mark.usefixtures("lvm_sr", "vm_on_lvm_sr")
class TestLVMSR:
    def test_start_and_shutdown_VM(self, vm_on_lvm_sr):
        vm = vm_on_lvm_sr
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    def test_snapshot(self, vm_on_lvm_sr):
        vm = vm_on_lvm_sr
        vm.start()
        vm.wait_for_os_booted()
        vm.test_snapshot_on_running_vm()
        vm.shutdown(verify=True)

    # *** tests with reboots (longer tests).

    def test_reboot(self, host, lvm_sr, vm_on_lvm_sr):
        sr = lvm_sr
        vm = vm_on_lvm_sr
        host.reboot(verify=True)
        wait_for(sr.all_pbds_attached, "Wait for PDB attached")
        # start the VM as a way to check that the underlying SR is operational
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    # *** End of tests with reboots
