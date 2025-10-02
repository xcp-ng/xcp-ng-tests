import pytest

from lib.common import vm_image, wait_for
from tests.storage.storage import install_randstream, operation_on_vdi, wait_for_vdi_coalesce

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.vdi import VDI
    from lib.vm import VM

# Requirements:
# - one XCP-ng host >= 8.3 with an additional unused disk for the SR
# - access to XCP-ng RPM repository from the host

pytestmark = pytest.mark.usefixtures("host_at_least_8_3")

class TestZfsvolSRCreateDestroy:
    """
    Tests that do not use fixtures that setup the SR or import VMs,
    because they precisely need to test SR creation and destruction,
    and VM import.
    """

    def test_create_and_destroy_sr(self, sr_disk_wiped, host_with_zfsvol):
        host = host_with_zfsvol
        # Create and destroy tested in the same test to leave the host as unchanged as possible
        sr = host.sr_create('zfs-vol', "ZFS-local-SR-test", {'device': '/dev/' + sr_disk_wiped}, verify=True)
        # import a VM in order to detect vm import issues here rather than in the vm_on_xfs_fixture used in
        # the next tests, because errors in fixtures break teardown
        vm = host.import_vm(vm_image('mini-linux-x86_64-bios'), sr_uuid=sr.uuid)
        vm.destroy(verify=True)
        sr.destroy(verify=True)

@pytest.mark.usefixtures("zfsvol_sr")
class TestZfsvolVm:

    @pytest.mark.xfail
    @pytest.mark.quicktest
    def test_quicktest(self, zfsvol_sr):
        zfsvol_sr.run_quicktest()

    @pytest.mark.small_vm # run with a small VM to test the features
    @pytest.mark.big_vm # and ideally with a big VM to test it scales
    def test_start_and_shutdown_VM(self, vm_on_zfsvol_sr):
        vm = vm_on_zfsvol_sr
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    @pytest.mark.xfail # needs support for destroying snapshots
    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_snapshot(self, vm_on_zfsvol_sr):
        vm = vm_on_zfsvol_sr
        vm.start()
        try:
            vm.wait_for_os_booted()
            vm.test_snapshot_on_running_vm()
        finally:
            vm.shutdown(verify=True)

    @pytest.mark.small_vm
    @pytest.mark.parametrize("vdi_op", ["snapshot"])  # "clone" requires a snapshot
    def test_coalesce(self, unix_vm: 'VM', vdi_on_zfsvol_sr: 'VDI', vdi_op):
        vm = unix_vm
        vdi = vdi_on_zfsvol_sr
        vm.connect_vdi(vdi, 'xvdb')
        new_vdi = None
        try:
            vm.start()
            vm.wait_for_vm_running_and_ssh_up()
            install_randstream(vm)
            vm.ssh("randstream generate -v /dev/xvdb")
            vm.ssh("randstream validate -v --expected-checksum 65280014 /dev/xvdb")
            new_vdi = operation_on_vdi(vm.host, vdi.uuid, vdi_op)
            vm.ssh("randstream generate -v --seed 1 --size 128Mi /dev/xvdb")
            vm.ssh("randstream validate -v --expected-checksum ad2ca9af /dev/xvdb")
            vm.host.xe("vdi-destroy", {"uuid": new_vdi.uuid})
            new_vdi = None
            wait_for_vdi_coalesce(vdi)
            vm.ssh("randstream validate -v --expected-checksum ad2ca9af /dev/xvdb")
        finally:
            vm.shutdown()
            vm.disconnect_vdi(vdi)
            if new_vdi is not None:
                new_vdi.destroy()

    # *** tests with reboots (longer tests).

    @pytest.mark.reboot
    @pytest.mark.small_vm
    def test_reboot(self, vm_on_zfsvol_sr, host, zfsvol_sr):
        sr = zfsvol_sr
        vm = vm_on_zfsvol_sr
        host.reboot(verify=True)
        wait_for(sr.all_pbds_attached, "Wait for PBD attached")
        # start the VM as a way to check that the underlying SR is operational
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    # *** End of tests with reboots
