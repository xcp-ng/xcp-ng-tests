import pytest

from lib.commands import SSHCommandFailed
from lib.common import Defer, vm_image, wait_for
from lib.sr import SR
from lib.vdi import VDI, ImageFormat
from lib.vm import VM
from tests.storage import (
    MAX_VDI_SIZE,
    CoalesceOperation,
    ImageFormat,
    XVACompression,
    coalesce_integrity,
    full_vdi_write,
    vdi_export_import,
    vdi_is_open,
    xva_export_import,
)

# Requirements:
# - one XCP-ng host >= 8.2
# - a valid lvmohba config

class TestLVMOHBASRCreateDestroy:
    """
    Tests that do not use fixtures that setup the SR or import VMs,
    because they precisely need to test SR creation and destruction,
    and VM import.
    """

    def test_create_and_destroy_sr(self, host, lvmohba_device_config, image_format: ImageFormat):
        # Create and destroy tested in the same test to leave the host as unchanged as possible
        sr = host.sr_create('lvmohba', "lvmohba-SR-test",
                            lvmohba_device_config | {'preferred-image-formats': image_format},
                            shared=True, verify=True)
        # import a VM in order to detect vm import issues here rather than in the vm_on_xfs_fixture used in
        # the next tests, because errors in fixtures break teardown
        vm = host.import_vm(vm_image('mini-linux-x86_64-bios'), sr_uuid=sr.uuid)
        vm.destroy(verify=True)
        sr.destroy(verify=True)

@pytest.mark.usefixtures('image_format')
@pytest.mark.usefixtures("lvmohba_sr")
@pytest.mark.thick_provisioned
class TestLVMOHBASR:
    @pytest.mark.quicktest
    def test_quicktest(self, lvmohba_sr):
        lvmohba_sr.run_quicktest()

    def test_vdi_is_not_open(self, vdi_on_lvmohba_sr):
        assert not vdi_is_open(vdi_on_lvmohba_sr)

    @pytest.mark.small_vm # run with a small VM to test the features
    @pytest.mark.big_vm # and ideally with a big VM to test it scales
    def test_start_and_shutdown_VM(self, vm_on_lvmohba_sr):
        vm = vm_on_lvmohba_sr
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_snapshot(self, vm_on_lvmohba_sr):
        vm = vm_on_lvmohba_sr
        vm.start()
        try:
            vm.wait_for_os_booted()
            vm.test_snapshot_on_running_vm()
        finally:
            vm.shutdown(verify=True)

    @pytest.mark.small_vm
    @pytest.mark.parametrize("vdi_op", ["snapshot", "clone"])
    def test_coalesce(self, storage_test_vm: 'VM', vdi_on_lvmohba_sr: 'VDI', vdi_op: CoalesceOperation, defer: Defer):
        coalesce_integrity(storage_test_vm, vdi_on_lvmohba_sr, vdi_op, defer)

    @pytest.mark.small_vm
    @pytest.mark.disk_throughput_intensive
    def test_full_vdi_write(self, storage_test_vm: VM, vdi_on_lvmohba_sr: VDI, defer: Defer):
        full_vdi_write(storage_test_vm, vdi_on_lvmohba_sr, defer)

    @pytest.mark.small_vm
    def test_invalid_vdi_size(self, lvmohba_sr: SR, image_format: ImageFormat):
        with pytest.raises(SSHCommandFailed) as excinfo:
            lvmohba_sr.create_vdi(virtual_size=MAX_VDI_SIZE[image_format] + 1)
        assert 'VDI Invalid size' in excinfo.value.stdout

    @pytest.mark.small_vm
    @pytest.mark.parametrize("compression", ["none", "gzip", "zstd"])
    def test_xva_export_import(self, vm_on_lvmohba_sr: VM, compression: XVACompression, temp_large_dir: str,
                               defer: Defer):
        xva_export_import(vm_on_lvmohba_sr, compression, temp_large_dir, defer)

    @pytest.mark.small_vm
    def test_vdi_export_import(self, storage_test_vm: VM, lvmohba_sr: SR, image_format: ImageFormat,
                               temp_large_dir: str, defer: Defer):
        vdi_export_import(storage_test_vm, lvmohba_sr, image_format, temp_large_dir, defer)

    # *** tests with reboots (longer tests).

    @pytest.mark.reboot
    @pytest.mark.small_vm
    def test_reboot(self, host, lvmohba_sr, vm_on_lvmohba_sr):
        sr = lvmohba_sr
        vm = vm_on_lvmohba_sr
        host.reboot(verify=True)
        wait_for(sr.all_pbds_attached, "Wait for PBD attached")
        # start the VM as a way to check that the underlying SR is operational
        vm.start(on=host.uuid)
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    # *** End of tests with reboots
