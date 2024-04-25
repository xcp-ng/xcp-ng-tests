import pytest
from lib.common import wait_for, vm_image
from tests.storage import vdi_is_open

# Requirements:
# - one XCP-ng host >= 8.0 with an additional unused disk for the SR

# Make sure this fixture is called before the parametrized one
@pytest.mark.usefixtures('sr_device_config')
class TestNFSSRCreateDestroy:
    def test_create_and_destroy_sr(self, host, nfsng_device_config):
        device_config = nfsng_device_config
        # Create and destroy tested in the same test to leave the host as unchanged as possible
        sr = host.sr_create('nfs-ng', "NFS-SR-test", device_config, shared=True, verify=True)
        # import a VM in order to detect vm import issues here rather than in the vm_on_nfs fixture used in
        # the next tests, because errors in fixtures break teardown
        vm = host.import_vm(vm_image('mini-linux-x86_64-bios'), sr_uuid=sr.uuid)
        vm.destroy(verify=True)
        sr.destroy(verify=True)

# Make sure these fixtures are called before the parametrized one
@pytest.mark.usefixtures('sr_device_config', 'hosts')
class TestNFSSR:
    @pytest.mark.quicktest
    def test_quicktest(self, nfsng_sr):
        sr = nfsng_sr
        sr.run_quicktest()

    def test_vdi_is_not_open(self, vdi_on_nfsng_sr):
        vdi = vdi_on_nfsng_sr
        assert not vdi_is_open(vdi)

    @pytest.mark.small_vm # run with a small VM to test the features
    @pytest.mark.big_vm # and ideally with a big VM to test it scales
    # Make sure this fixture is called before the parametrized one
    @pytest.mark.usefixtures('vm_ref')
    def test_start_and_shutdown_VM(self, vm_on_nfsng_sr):
        vm = vm_on_nfsng_sr
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    # Make sure this fixture is called before the parametrized one
    @pytest.mark.usefixtures('vm_ref')
    def test_snapshot(self, vm_on_nfsng_sr):
        vm = vm_on_nfsng_sr
        vm.start()
        try:
            vm.wait_for_os_booted()
            vm.test_snapshot_on_running_vm()
        finally:
            vm.shutdown(verify=True)

    # *** tests with reboots (longer tests).

    @pytest.mark.reboot
    @pytest.mark.small_vm
    # Make sure this fixture is called before the parametrized one
    @pytest.mark.usefixtures('vm_ref')
    def test_reboot(self, host, vm_on_nfsng_sr):
        vm = vm_on_nfsng_sr
        sr = vm.get_sr()
        host.reboot(verify=True)
        wait_for(sr.all_pbds_attached, "Wait for PBD attached")
        # start the VM as a way to check that the underlying SR is operational
        vm.start(on=host.uuid)
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    # *** End of tests with reboots
