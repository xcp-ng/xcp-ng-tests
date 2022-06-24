import pytest
from lib.common import wait_for, vm_image

# Requirements:
# - one XCP-ng host >= 8.2
# - a valid lvmoiscsi config

class TestLVMOISCSISRCreateDestroy:
    """
    Tests that do not use fixtures that setup the SR or import VMs,
    because they precisely need to test SR creation and destruction,
    and VM import.
    """

    def test_create_and_destroy_sr(self, host, lvmoiscsi_device_config):
        # Create and destroy tested in the same test to leave the host as unchanged as possible
        sr = host.sr_create('lvmoiscsi', "lvmoiscsi-SR-test", lvmoiscsi_device_config, shared=True, verify=True)
        # import a VM in order to detect vm import issues here rather than in the vm_on_xfs_fixture used in
        # the next tests, because errors in fixtures break teardown
        vm = host.import_vm(vm_image('mini-linux-x86_64-bios'), sr_uuid=sr.uuid)
        vm.destroy(verify=True)
        sr.destroy(verify=True)


@pytest.mark.usefixtures("lvmoiscsi_sr", "vm_on_lvmoiscsi_sr")
class TestLVMOISCSISR:
    @pytest.mark.small_vm # run with a small VM to test the features
    @pytest.mark.big_vm # and ideally with a big VM to test it scales
    def test_start_and_shutdown_VM(self, vm_on_lvmoiscsi_sr):
        vm = vm_on_lvmoiscsi_sr
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_snapshot(self, vm_on_lvmoiscsi_sr):
        vm = vm_on_lvmoiscsi_sr
        vm.start()
        vm.wait_for_os_booted()
        vm.test_snapshot_on_running_vm()
        vm.shutdown(verify=True)

    # *** tests with reboots (longer tests).

    @pytest.mark.reboot
    @pytest.mark.small_vm
    def test_reboot(self, host, lvmoiscsi_sr, vm_on_lvmoiscsi_sr):
        sr = lvmoiscsi_sr
        vm = vm_on_lvmoiscsi_sr
        host.reboot(verify=True)
        wait_for(sr.all_pbds_attached, "Wait for PBD attached")
        # start the VM as a way to check that the underlying SR is operational
        vm.start(on=host.uuid)
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    # *** End of tests with reboots
