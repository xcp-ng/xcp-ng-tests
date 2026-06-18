from __future__ import annotations

import pytest

import logging
import time

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
    assert_cbt_log_does_not_exist_file_sr,
    assert_cbt_log_exists_file_sr,
    coalesce_integrity,
    full_vdi_write,
    vdi_export_import,
    vdi_is_open,
    xva_export_import,
)

# Requirements:
# - one XCP-ng host >= 8.2 with an additional unused disk for the SR
# - access to XCP-ng RPM repository from the host

@pytest.mark.usefixtures("xfs_sr")
class TestXFSSR:
    @pytest.mark.quicktest
    def test_quicktest(self, xfs_sr: SR) -> None:
        xfs_sr.run_quicktest()

    def test_vdi_is_not_open(self, vdi_on_xfs_sr: VDI) -> None:
        assert not vdi_is_open(vdi_on_xfs_sr)

    def test_vdi_image_format(self, vdi_on_xfs_sr: VDI, image_format: ImageFormat) -> None:
        fmt = vdi_on_xfs_sr.get_image_format()
        # feature-detect: if the SM doesn't report image-format, skip this check
        if not fmt:
            pytest.skip("SM does not report sm-config:image-format; skipping format check")
        assert fmt == image_format

    @pytest.mark.small_vm # run with a small VM to test the features
    @pytest.mark.big_vm # and ideally with a big VM to test it scales
    def test_start_and_shutdown_VM(self, vm_on_xfs_sr: VM) -> None:
        vm = vm_on_xfs_sr
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_snapshot(self, vm_on_xfs_sr: VM) -> None:
        vm = vm_on_xfs_sr
        vm.start()
        try:
            vm.wait_for_os_booted()
            vm.test_snapshot_on_running_vm()
        finally:
            vm.shutdown(verify=True)

    @pytest.mark.small_vm
    @pytest.mark.parametrize("vdi_op", ["snapshot", "clone"])
    def test_coalesce(self, storage_test_vm: VM, vdi_on_xfs_sr: VDI, vdi_op: CoalesceOperation, defer: Defer) -> None:
        coalesce_integrity(storage_test_vm, vdi_on_xfs_sr, vdi_op, defer)

    @pytest.mark.small_vm
    @pytest.mark.disk_throughput_intensive
    def test_full_vdi_write(self, storage_test_vm: VM, vdi_on_xfs_sr: VDI, defer: Defer):
        full_vdi_write(storage_test_vm, vdi_on_xfs_sr, defer)

    @pytest.mark.small_vm
    def test_invalid_vdi_size(self, xfs_sr: SR, image_format: ImageFormat):
        with pytest.raises(SSHCommandFailed) as excinfo:
            xfs_sr.create_vdi(virtual_size=MAX_VDI_SIZE[image_format] + 1)
        assert 'VDI Invalid size' in excinfo.value.stdout

    @pytest.mark.small_vm
    @pytest.mark.parametrize("compression", ["none", "gzip", "zstd"])
    def test_xva_export_import(self, vm_on_xfs_sr: VM, compression: XVACompression, temp_large_dir: str, defer: Defer) \
            -> None:
        xva_export_import(vm_on_xfs_sr, compression, temp_large_dir, defer)

    @pytest.mark.small_vm
    def test_vdi_export_import(self, storage_test_vm: VM, xfs_sr: SR, image_format: ImageFormat, temp_large_dir: str,
                               defer: Defer) -> None:
        vdi_export_import(storage_test_vm, xfs_sr, image_format, temp_large_dir, defer)

    # *** tests with reboots (longer tests).

    @pytest.mark.reboot
    @pytest.mark.small_vm
    def test_reboot(self, vm_on_xfs_sr: VM, host: Host, xfs_sr: SR) -> None:
        sr = xfs_sr
        vm = vm_on_xfs_sr
        host.reboot(verify=True)
        wait_for(sr.all_pbds_attached, "Wait for PBD attached")
        # start the VM as a way to check that the underlying SR is operational
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    @pytest.mark.reboot
    def test_xfsprogs_missing(self, host: Host, xfs_sr: SR) -> None:
        sr = xfs_sr
        xfsprogs_installed = True
        try:
            host.yum_remove(['xfsprogs'])
            xfsprogs_installed = False
            try:
                sr.scan()
                assert False, "SR scan should have failed"
            except SSHCommandFailed:
                logging.info("SR scan failed as expected.")
            host.reboot(verify=True)
            # give the host some time to try to attach the SR
            time.sleep(10)
            logging.info("Assert PBD not attached")
            assert not sr.all_pbds_attached()
            host.yum_install(['xfsprogs'])
            xfsprogs_installed = True
            sr.plug_pbds(verify=True)
            sr.scan()
        finally:
            if not xfsprogs_installed:
                host.yum_install(['xfsprogs'])

    # *** End of tests with reboots


class TestXFSCBT(CBTTest):
    """Test CBT functionality on XFS SR"""

    @staticmethod
    def assert_cbt_log_exists(host: Host, sr: SR, vdi: VDI) -> None:
        assert_cbt_log_exists_file_sr(host, sr, vdi)

    @staticmethod
    def assert_cbt_log_does_not_exist(host: Host, sr: SR, vdi: VDI) -> None:
        assert_cbt_log_does_not_exist_file_sr(host, sr, vdi)

    def test_enable_disable_cbt(self, host: Host, xfs_sr: SR, vdi_on_xfs_sr: VDI) -> None:
        self._test_enable_disable_cbt(host, xfs_sr, vdi_on_xfs_sr)

    def test_cbt_log_creation(self, host: Host, xfs_sr: SR, vdi_on_xfs_sr: VDI) -> None:
        self._test_cbt_log_creation(host, xfs_sr, vdi_on_xfs_sr)

    def test_snapshot_with_cbt(self, host: Host, xfs_sr: SR, vdi_on_xfs_sr: VDI) -> None:
        self._test_snapshot_with_cbt(host, xfs_sr, vdi_on_xfs_sr)

    @pytest.mark.small_vm
    def test_changed_blocks_tracking(self, host: Host, xfs_sr: SR, vdi_on_xfs_sr: VDI, vm_on_xfs_sr: VM) -> None:
        self._test_changed_blocks_tracking(host, xfs_sr, vdi_on_xfs_sr, vm_on_xfs_sr)

    @pytest.mark.small_vm
    def test_cbt_after_coalesce(self, host: Host, xfs_sr: SR, vdi_on_xfs_sr: VDI, vm_on_xfs_sr: VM) -> None:
        self._test_cbt_after_coalesce(host, xfs_sr, vdi_on_xfs_sr, vm_on_xfs_sr)

    @pytest.mark.small_vm
    def test_incremental_snap_scenario(self, host: Host, xfs_sr: SR, vdi_on_xfs_sr: VDI, vm_on_xfs_sr: VM) -> None:
        self._test_incremental_snap_scenario(host, xfs_sr, vdi_on_xfs_sr, vm_on_xfs_sr)

    def test_disable_cbt_removes_log(self, host: Host, xfs_sr: SR, vdi_on_xfs_sr: VDI) -> None:
        self._test_disable_cbt_removes_log(host, xfs_sr, vdi_on_xfs_sr)

    def test_destroy_vdi_removes_cbt_log(self, host: Host, xfs_sr: SR, vdi_on_xfs_sr: VDI) -> None:
        self._test_destroy_vdi_removes_cbt_log(host, xfs_sr, vdi_on_xfs_sr)

    def test_cbt_persist_after_sr_reboot(self, host: Host, xfs_sr: SR, vdi_on_xfs_sr: VDI) -> None:
        self._test_cbt_persist_after_sr_reboot(host, xfs_sr, vdi_on_xfs_sr)

    def test_cbt_on_snapshot_chain(self, host: Host, xfs_sr: SR, vdi_on_xfs_sr: VDI) -> None:
        self._test_cbt_on_snapshot_chain(host, xfs_sr, vdi_on_xfs_sr)

    def test_cbt_parent_disable_does_not_affect_snapshot(self, host: Host, xfs_sr: SR, vdi_on_xfs_sr: VDI) -> None:
        self._test_cbt_parent_disable_does_not_affect_snapshot(host, xfs_sr, vdi_on_xfs_sr)

    @pytest.mark.small_vm
    def test_cbt_bitmap_non_zero_after_write(self, host: Host, xfs_sr: SR, vdi_on_xfs_sr: VDI,
                                             vm_on_xfs_sr: VM) -> None:
        self._test_cbt_bitmap_non_zero_after_write(host, xfs_sr, vdi_on_xfs_sr, vm_on_xfs_sr)

    def test_cbt_data_destroy(self, host: Host, xfs_sr: SR, vdi_on_xfs_sr: VDI) -> None:
        self._test_cbt_data_destroy(host, xfs_sr, vdi_on_xfs_sr)
