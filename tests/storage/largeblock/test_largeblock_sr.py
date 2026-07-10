from __future__ import annotations

import pytest

from lib.common import vm_image, wait_for
from lib.vdi import ImageFormat
from tests.storage import try_to_create_sr_with_missing_device, vdi_is_open

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.host import Host
    from lib.sr import SR
    from lib.vdi import VDI
    from lib.vm import VM

# Requirements:
# - one XCP-ng host with an additional unused 4KiB disk for the SR

@pytest.mark.usefixtures("largeblock_sr")
class TestLARGEBLOCKSR:
    @pytest.mark.quicktest
    def test_quicktest(self, largeblock_sr: SR) -> None:
        largeblock_sr.run_quicktest()

    def test_vdi_is_not_open(self, vdi_on_largeblock_sr: VDI) -> None:
        assert not vdi_is_open(vdi_on_largeblock_sr)

    def test_vdi_image_format(self, vdi_on_largeblock_sr: VDI, image_format: ImageFormat) -> None:
        fmt = vdi_on_largeblock_sr.get_image_format()
        # feature-detect: if the SM doesn't report image-format, skip this check
        if not fmt:
            pytest.skip("SM does not report sm-config:image-format; skipping format check")
        assert fmt == image_format

    @pytest.mark.small_vm # run with a small VM to test the features
    @pytest.mark.big_vm # and ideally with a big VM to test it scales
    def test_start_and_shutdown_VM(self, vm_on_largeblock_sr: VM) -> None:
        vm = vm_on_largeblock_sr
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_snapshot(self, vm_on_largeblock_sr: VM) -> None:
        vm = vm_on_largeblock_sr
        vm.start()
        vm.wait_for_os_booted()
        vm.test_snapshot_on_running_vm()
        vm.shutdown(verify=True)

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_revert(self, vm_on_largeblock_sr: VM) -> None:
        vm_on_largeblock_sr.test_vdi_revert()

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_revert_cbt(self, vm_on_largeblock_sr: VM) -> None:
        vm_on_largeblock_sr.test_vdi_revert_cbt()

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_revert_journal_cbt(self, vm_on_largeblock_sr: VM, request: pytest.FixtureRequest):
        vm_on_largeblock_sr.test_vdi_revert_journal_cbt(request, "FileSR_revert_create_src")

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    @pytest.mark.parametrize(
        "fistpoint",
        [
            "FileSR_revert_create_insert",
            "FileSR_revert_create_src",
            "FileSR_revert_create_dest",
        ]
    )
    def test_revert_journal(self, vm_on_largeblock_sr: VM, request: pytest.FixtureRequest, fistpoint: str):
        vm_on_largeblock_sr.test_vdi_revert_journal(request, fistpoint)

    # *** tests with reboots (longer tests).

    @pytest.mark.reboot
    @pytest.mark.small_vm
    def test_reboot(self, host: Host, largeblock_sr: SR, vm_on_largeblock_sr: VM) -> None:
        sr = largeblock_sr
        vm = vm_on_largeblock_sr
        host.reboot(verify=True)
        wait_for(sr.all_pbds_attached, "Wait for PBD attached")
        # start the VM as a way to check that the underlying SR is operational
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    # *** End of tests with reboots
