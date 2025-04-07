import pytest
from lib.common import wait_for, vm_image
from tests.storage import try_to_create_sr_with_missing_device, vdi_is_open

# Requirements:
# - one XCP-ng host with an additional unused disk for the SR

class TestGENSRCreateDestroy:
    """
    Tests that do not use fixtures that setup the SR or import VMs,
    because they precisely need to test SR creation and destruction,
    and VM import.
    """

    def test_create_sr_with_missing_device(self, host, sr_type):
        sr_name = "{}-local-SR-test".format(sr_type.upper())
        try_to_create_sr_with_missing_device(sr_type, sr_name, host)

    def test_create_and_destroy_sr(self, host, sr_disk, sr_type):
        # Create and destroy tested in the same test to leave the host as unchanged as possible
        sr_name = "{}-local-SR-test".format(sr_type.upper())
        sr = host.sr_create(sr_type, sr_name, {'device': '/dev/' + sr_disk}, verify=True)
        # import a VM in order to detect vm import issues here rather than in the vm_on_xfs_fixture used in
        # the next tests, because errors in fixtures break teardown
        vm = host.import_vm(vm_image('mini-linux-x86_64-bios'), sr_uuid=sr.uuid)
        vm.destroy(verify=True)
        sr.destroy(verify=True)

@pytest.mark.usefixtures("generic_sr")
class TestGENSR:
    @pytest.mark.quicktest
    def test_quicktest(self, generic_sr):
        generic_sr.run_quicktest()

    def test_vdi_is_not_open(self, vdi_on_generic_sr):
        assert not vdi_is_open(vdi_on_generic_sr)

    @pytest.mark.small_vm # run with a small VM to test the features
    @pytest.mark.big_vm # and ideally with a big VM to test it scales
    def test_start_and_shutdown_VM(self, vm_on_generic_sr):
        vm = vm_on_generic_sr
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_snapshot(self, vm_on_generic_sr):
        vm = vm_on_generic_sr
        vm.start()
        try:
            vm.wait_for_os_booted()
            vm.test_snapshot_on_running_vm()
        finally:
            vm.shutdown(verify=True)

    # *** tests with reboots (longer tests).

    @pytest.mark.reboot
    @pytest.mark.small_vm
    def test_reboot(self, host, generic_sr, vm_on_generic_sr):
        sr = generic_sr
        vm = vm_on_generic_sr
        host.reboot(verify=True)
        wait_for(sr.all_pbds_attached, "Wait for PBD attached")
        # start the VM as a way to check that the underlying SR is operational
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    # *** End of tests with reboots
