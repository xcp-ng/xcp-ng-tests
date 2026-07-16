import pytest

from lib.commands import SSHCommandFailed
from lib.common import Defer, vm_image, wait_for
from lib.host import Host
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
from tests.storage.storage import (
    check_critical_journal_revert,
    check_vdi_revert,
    check_vdi_revert_cbt,
    check_vdi_revert_journal,
    check_vdi_revert_journal_cbt,
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

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_revert(self, vm_on_lvmohba_sr: VM, defer: Defer) -> None:
        check_vdi_revert(defer, vm_on_lvmohba_sr)

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_revert_cbt(self, vm_on_lvmohba_sr: VM, defer: Defer) -> None:
        check_vdi_revert_cbt(defer, vm_on_lvmohba_sr)

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_revert_journal_cbt(self, vm_on_lvmohba_sr: VM, defer: Defer, exit_on_fistpoint: None):
        check_vdi_revert_journal_cbt(
            defer, vm_on_lvmohba_sr, "LVM_revert_create_src", vm_on_lvmohba_sr.host.pool.master
        )

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    @pytest.mark.parametrize(
        "fistpoint",
        [
            "LVM_revert_create_insert",
            "LVM_revert_create_src",
            "LVM_revert_create_dest",
        ]
    )
    def test_revert_journal(self, vm_on_lvmohba_sr: VM, defer: Defer, exit_on_fistpoint: None, fistpoint: str):
        check_vdi_revert_journal(defer, vm_on_lvmohba_sr, fistpoint, vm_on_lvmohba_sr.host.pool.master)

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_critical_journal_revert(
        self, vm_on_lvmohba_sr: VM, defer: Defer, exit_on_fistpoint: None, hostA2: Host
    ) -> None:
        check_critical_journal_revert(defer, vm_on_lvmohba_sr, hostA2, "LVM_revert_create_src")

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
