import pytest

from lib.commands import SSHCommandFailed
from lib.common import Defer, vm_image, wait_for
from lib.host import Host
from lib.sr import SR
from lib.vdi import VDI
from lib.vm import VM
from tests.storage import (
    MAX_VDI_SIZE,
    CBTTest,
    CoalesceOperation,
    ImageFormat,
    XVACompression,
    assert_cbt_log_does_not_exist_lvm_sr,
    assert_cbt_log_exists_lvm_sr,
    coalesce_integrity,
    full_vdi_write,
    vdi_export_import,
    vdi_is_open,
    xva_export_import,
)

# Requirements:
# - one XCP-ng host >= 8.2
# - a valid lvmohba config

@pytest.mark.usefixtures('image_format')
@pytest.mark.usefixtures("lvmohba_sr")
@pytest.mark.thick_provisioned
class TestLVMOHBASR:
    @pytest.mark.quicktest
    def test_quicktest(self, lvmohba_sr):
        lvmohba_sr.run_quicktest()

    def test_vdi_is_not_open(self, vdi_on_lvmohba_sr: VDI):
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
    def test_xva_export_import_with_snapshot(self, vm_on_lvmohba_sr: VM, temp_large_dir: str, defer: Defer):
        xva_export_import(vm_on_lvmohba_sr, 'zstd', temp_large_dir, defer, with_snapshot=True)

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


class TestLVMoHBACBT(CBTTest):
    """Test CBT functionality on LVMOHBA SR"""

    @staticmethod
    def assert_cbt_log_exists(host: Host, sr: SR, vdi: VDI) -> None:
        assert_cbt_log_exists_lvm_sr(host, sr, vdi)

    @staticmethod
    def assert_cbt_log_does_not_exist(host: Host, sr: SR, vdi: VDI) -> None:
        assert_cbt_log_does_not_exist_lvm_sr(host, sr, vdi)

    def test_enable_disable_cbt(self, host: Host, lvmohba_sr: SR, vdi_on_lvmohba_sr: VDI) -> None:
        self._test_enable_disable_cbt(host, lvmohba_sr, vdi_on_lvmohba_sr)

    def test_cbt_log_creation(self, host: Host, lvmohba_sr: SR, vdi_on_lvmohba_sr: VDI) -> None:
        self._test_cbt_log_creation(host, lvmohba_sr, vdi_on_lvmohba_sr)

    def test_snapshot_with_cbt(self, host: Host, lvmohba_sr: SR, vdi_on_lvmohba_sr: VDI) -> None:
        self._test_snapshot_with_cbt(host, lvmohba_sr, vdi_on_lvmohba_sr)

    @pytest.mark.small_vm
    def test_changed_blocks_tracking(self, host: Host, lvmohba_sr: SR, vdi_on_lvmohba_sr: VDI,
                                     vm_on_lvmohba_sr: VM) -> None:
        self._test_changed_blocks_tracking(host, lvmohba_sr, vdi_on_lvmohba_sr, vm_on_lvmohba_sr)

    @pytest.mark.small_vm
    def test_cbt_after_coalesce(self, host: Host, lvmohba_sr: SR, vdi_on_lvmohba_sr: VDI,
                                vm_on_lvmohba_sr: VM) -> None:
        self._test_cbt_after_coalesce(host, lvmohba_sr, vdi_on_lvmohba_sr, vm_on_lvmohba_sr)

    @pytest.mark.small_vm
    def test_incremental_snap_scenario(self, host: Host, lvmohba_sr: SR, vdi_on_lvmohba_sr: VDI,
                                       vm_on_lvmohba_sr: VM) -> None:
        self._test_incremental_snap_scenario(host, lvmohba_sr, vdi_on_lvmohba_sr, vm_on_lvmohba_sr)

    def test_disable_cbt_removes_log(self, host: Host, lvmohba_sr: SR, vdi_on_lvmohba_sr: VDI) -> None:
        self._test_disable_cbt_removes_log(host, lvmohba_sr, vdi_on_lvmohba_sr)

    def test_destroy_vdi_removes_cbt_log(self, host: Host, lvmohba_sr: SR, vdi_on_lvmohba_sr: VDI) -> None:
        self._test_destroy_vdi_removes_cbt_log(host, lvmohba_sr, vdi_on_lvmohba_sr)

    def test_cbt_persist_after_sr_reboot(self, host: Host, lvmohba_sr: SR, vdi_on_lvmohba_sr: VDI) -> None:
        self._test_cbt_persist_after_sr_reboot(host, lvmohba_sr, vdi_on_lvmohba_sr)

    def test_cbt_on_snapshot_chain(self, host: Host, lvmohba_sr: SR, vdi_on_lvmohba_sr: VDI) -> None:
        self._test_cbt_on_snapshot_chain(host, lvmohba_sr, vdi_on_lvmohba_sr)

    def test_cbt_parent_disable_does_not_affect_snapshot(self, host: Host, lvmohba_sr: SR,
                                                         vdi_on_lvmohba_sr: VDI) -> None:
        self._test_cbt_parent_disable_does_not_affect_snapshot(host, lvmohba_sr, vdi_on_lvmohba_sr)

    @pytest.mark.small_vm
    def test_cbt_bitmap_non_zero_after_write(self, host: Host, lvmohba_sr: SR, vdi_on_lvmohba_sr: VDI,
                                             vm_on_lvmohba_sr: VM) -> None:
        self._test_cbt_bitmap_non_zero_after_write(host, lvmohba_sr, vdi_on_lvmohba_sr, vm_on_lvmohba_sr)

    def test_cbt_data_destroy(self, host: Host, lvmohba_sr: SR, vdi_on_lvmohba_sr: VDI) -> None:
        self._test_cbt_data_destroy(host, lvmohba_sr, vdi_on_lvmohba_sr)
