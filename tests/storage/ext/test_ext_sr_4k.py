from __future__ import annotations

import pytest

from lib.commands import SSHCommandFailed
from lib.common import Defer, vm_image, wait_for
from lib.fistpoint import FistPoint
from lib.host import Host
from lib.sr import SR
from lib.vdi import QCOW2_IMAGE_FORMAT, VDI
from lib.vm import VM
from tests.storage import (
    MAX_VDI_SIZE,
    CoalesceOperation,
    XVACompression,
    coalesce_integrity,
    full_vdi_write,
    try_to_create_sr_with_missing_device,
    vdi_export_import,
    vdi_is_open,
    xva_export_import,
)

# Requirements:
# - one XCP-ng host >= 8.3 with an additional unused native 4KiB disk for the SR

class TestEXTSR4KCreateDestroy:
    """
    Tests that do not use fixtures that setup the SR or import VMs,
    because they precisely need to test SR creation and destruction,
    and VM import.
    """

    def test_create_sr_with_missing_device(self, host: Host) -> None:
        try_to_create_sr_with_missing_device('ext', 'EXT-4K-local-SR-test', host)

    def test_create_and_destroy_sr(self, host: Host,
                                   unused_4k_disks: dict[Host, list[Host.BlockDeviceInfo]]) -> None:
        # Create and destroy tested in the same test to leave the host as unchanged as possible
        sr_disk = unused_4k_disks[host][0].name
        sr = host.sr_create('ext', "EXT-4K-local-SR-test",
                            {'device': '/dev/' + sr_disk,
                             'preferred-image-formats': QCOW2_IMAGE_FORMAT}, verify=True)
        # import a VM in order to detect vm import issues here rather than in the vm_on_xfs_fixture used in
        # the next tests, because errors in fixtures break teardown
        vm = host.import_vm(vm_image('mini-linux-x86_64-bios'), sr_uuid=sr.uuid)
        vm.destroy(verify=True)
        sr.destroy(verify=True)

@pytest.mark.usefixtures("ext_sr_4k")
class TestEXTSR4K:
    @pytest.mark.quicktest
    def test_quicktest(self, ext_sr_4k: SR) -> None:
        ext_sr_4k.run_quicktest()

    def test_vdi_is_not_open(self, vdi_on_ext_sr_4k: VDI) -> None:
        assert not vdi_is_open(vdi_on_ext_sr_4k)

    def test_vdi_image_format(self, vdi_on_ext_sr_4k: VDI) -> None:
        fmt = vdi_on_ext_sr_4k.get_image_format()
        # feature-detect: if the SM doesn't report image-format, skip this check
        if not fmt:
            pytest.skip("SM does not report sm-config:image-format; skipping format check")
        assert fmt == QCOW2_IMAGE_FORMAT

    @pytest.mark.small_vm # run with a small VM to test the features
    @pytest.mark.big_vm # and ideally with a big VM to test it scales
    def test_start_and_shutdown_VM(self, vm_on_ext_sr_4k: VM) -> None:
        vm = vm_on_ext_sr_4k
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_snapshot(self, vm_on_ext_sr_4k: VM) -> None:
        vm = vm_on_ext_sr_4k
        vm.start()
        try:
            vm.wait_for_os_booted()
            vm.test_snapshot_on_running_vm()
        finally:
            vm.shutdown(verify=True)

    @pytest.mark.small_vm
    @pytest.mark.parametrize("vdi_op", ["snapshot", "clone"])
    def test_coalesce(self, storage_test_vm: VM, vdi_on_ext_sr_4k: VDI, vdi_op: CoalesceOperation,
                      defer: Defer) -> None:
        coalesce_integrity(storage_test_vm, vdi_on_ext_sr_4k, vdi_op, defer)

    @pytest.mark.small_vm
    @pytest.mark.parametrize("compression", ["none", "gzip", "zstd"])
    def test_xva_export_import(self, vm_on_ext_sr_4k: VM, compression: XVACompression, temp_large_dir: str,
                               defer: Defer) -> None:
        xva_export_import(vm_on_ext_sr_4k, compression, temp_large_dir, defer)

    @pytest.mark.small_vm
    def test_vdi_export_import(self, storage_test_vm: VM, ext_sr_4k: SR, temp_large_dir: str,
                               defer: Defer) -> None:
        vdi_export_import(storage_test_vm, ext_sr_4k, QCOW2_IMAGE_FORMAT, temp_large_dir, defer)

    @pytest.mark.small_vm
    @pytest.mark.disk_throughput_intensive
    def test_full_vdi_write(self, storage_test_vm: VM, vdi_on_ext_sr_4k: VDI, defer: Defer):
        full_vdi_write(storage_test_vm, vdi_on_ext_sr_4k, defer)

    @pytest.mark.small_vm
    def test_invalid_vdi_size(self, ext_sr_4k: SR):
        with pytest.raises(SSHCommandFailed) as excinfo:
            ext_sr_4k.create_vdi(virtual_size=MAX_VDI_SIZE[QCOW2_IMAGE_FORMAT] + 1)
        assert 'VDI Invalid size' in excinfo.value.stdout

    # *** tests with blktap activate failure (longer tests).

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_blktap_activate_failure(self, vm_on_ext_sr_4k: VM) -> None:
        vm = vm_on_ext_sr_4k
        with FistPoint(vm.host, "blktap_activate_inject_failure"), pytest.raises(SSHCommandFailed):
            vm.start()
            vm.shutdown(force=True)

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_resize(self, vm_on_ext_sr_4k: VM) -> None:
        vm = vm_on_ext_sr_4k
        vdi = VDI(vm.vdi_uuids()[0], host=vm.host)
        old_size = vdi.get_virtual_size()
        new_size = old_size + (1 * 1024 * 1024 * 1024) # Adding a 1GiB to size

        vdi.resize(new_size)

        assert vdi.get_virtual_size() == new_size

    @pytest.mark.reboot
    @pytest.mark.small_vm
    def test_reboot(self, host: Host, ext_sr_4k: SR, vm_on_ext_sr_4k: VM) -> None:
        sr = ext_sr_4k
        vm = vm_on_ext_sr_4k
        host.reboot(verify=True)
        wait_for(sr.all_pbds_attached, "Wait for PBD attached")
        # start the VM as a way to check that the underlying SR is operational
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    # *** End of tests with reboots
