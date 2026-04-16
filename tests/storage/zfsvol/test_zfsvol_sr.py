from __future__ import annotations

import pytest

import logging

from lib import config
from lib.commands import SSHCommandFailed
from lib.common import Defer, GiB, KiB, MiB, vm_image, wait_for
from lib.host import Host
from lib.sr import SR
from lib.vdi import VDI
from lib.vm import VM
from tests.storage import (
    MAX_VDI_SIZE,
    CoalesceOperation,
    ImageFormat,
    XVACompression,
    coalesce_integrity,
    full_vdi_write,
    vdi_export_import,
    xva_export_import,
)

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

    def test_create_and_destroy_sr(self, sr_disk_wiped: str, host_with_zfsvol: Host) -> None:
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
    def test_quicktest(self, zfsvol_sr: SR) -> None:
        zfsvol_sr.run_quicktest()

    @pytest.mark.small_vm # run with a small VM to test the features
    @pytest.mark.big_vm # and ideally with a big VM to test it scales
    def test_start_and_shutdown_VM(self, vm_on_zfsvol_sr: VM) -> None:
        vm = vm_on_zfsvol_sr
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    @pytest.mark.xfail # needs support for destroying snapshots
    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_snapshot(self, vm_on_zfsvol_sr: VM) -> None:
        vm = vm_on_zfsvol_sr
        vm.start()
        try:
            vm.wait_for_os_booted()
            vm.test_snapshot_on_running_vm()
        finally:
            vm.shutdown(verify=True)

    @pytest.mark.small_vm
    @pytest.mark.parametrize("vdi_op", ["snapshot"])  # "clone" requires a snapshot
    @pytest.mark.skip("zfsvol doesn't provide vhd-parent")
    def test_coalesce(self, storage_test_vm: VM, vdi_on_zfsvol_sr: VDI, vdi_op: CoalesceOperation, defer: Defer) \
            -> None:
        coalesce_integrity(storage_test_vm, vdi_on_zfsvol_sr, vdi_op, defer)

    @pytest.mark.small_vm
    @pytest.mark.disk_throughput_intensive
    def test_full_vdi_write(self, storage_test_vm: VM, vdi_on_zfsvol_sr: VDI, defer: Defer):
        full_vdi_write(storage_test_vm, vdi_on_zfsvol_sr, defer)

    @pytest.mark.small_vm
    @pytest.mark.xfail(reason="not implemented yet")
    def test_invalid_vdi_size(self, zfsvol_sr: SR, image_format: ImageFormat):
        with pytest.raises(SSHCommandFailed) as excinfo:
            zfsvol_sr.create_vdi(virtual_size=MAX_VDI_SIZE[image_format] + 1)
        assert 'VDI Invalid size' in excinfo.value.stdout

    @pytest.mark.small_vm
    @pytest.mark.parametrize("compression", ["none", "gzip", "zstd"])
    def test_xva_export_import(self, vm_on_zfsvol_sr: VM, compression: XVACompression, temp_large_dir: str,
                               defer: Defer) -> None:
        if config.write_volume_cap > 20 * GiB:
            pytest.skip("Skipping large VDI test (known performance issue)")
        xva_export_import(vm_on_zfsvol_sr, compression, temp_large_dir, defer)

    @pytest.mark.small_vm
    def test_vdi_export_import(self, storage_test_vm: VM, zfsvol_sr: SR, image_format: ImageFormat, temp_large_dir: str,
                               defer: Defer) -> None:
        if config.write_volume_cap > 20 * GiB:
            pytest.skip("Skipping large VDI test (known performance issue)")
        vm = storage_test_vm
        sr = zfsvol_sr
        vdi_export_import(vm, sr, image_format, temp_large_dir, defer)

    # *** tests with reboots (longer tests).

    @pytest.mark.reboot
    @pytest.mark.small_vm
    def test_reboot(self, vm_on_zfsvol_sr: VM, host: Host, zfsvol_sr: SR) -> None:
        sr = zfsvol_sr
        vm = vm_on_zfsvol_sr
        host.reboot(verify=True)
        wait_for(sr.all_pbds_attached, "Wait for PBD attached")
        # start the VM as a way to check that the underlying SR is operational
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    # *** End of tests with reboots
