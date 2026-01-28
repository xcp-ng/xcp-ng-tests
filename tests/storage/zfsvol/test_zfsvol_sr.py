from __future__ import annotations

import pytest

import logging

from lib.common import vm_image, wait_for
from lib.sr import SR
from lib.vdi import VDI
from lib.vm import VM
from tests.storage import CoalesceOperation, ImageFormat, XVACompression, coalesce_integrity, xva_export_import

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
    def test_coalesce(self, storage_test_vm: VM, vdi_on_zfsvol_sr: VDI, vdi_op: CoalesceOperation):
        coalesce_integrity(storage_test_vm, vdi_on_zfsvol_sr, vdi_op)

    @pytest.mark.small_vm
    @pytest.mark.parametrize("compression", ["none", "gzip", "zstd"])
    def test_xva_export_import(self, vm_on_zfsvol_sr: VM, compression: XVACompression):
        xva_export_import(vm_on_zfsvol_sr, compression)

    @pytest.mark.small_vm
    def test_vdi_export_import(self, storage_test_vm: VM, zfsvol_sr: SR, image_format: ImageFormat):
        vm = storage_test_vm
        sr = zfsvol_sr
        vdi = sr.create_vdi(None, image_format=image_format)
        image_path = f'/tmp/{vdi.uuid}.{image_format}'
        try:
            vbd = vm.connect_vdi(vdi)
            dev = f'/dev/{vbd.param_get("device")}'
            # generate 2 blocks of data of 200MiB, at position 0 and at position 500MiB
            vm.ssh(f"randstream generate -v --size 200MiB {dev}")
            vm.ssh(f"randstream generate -v --seed 1 --position 500MiB --size 200MiB {dev}")
            vm.ssh(f"randstream validate -v --size 200MiB --expected-checksum c6310c52 {dev}")
            vm.ssh(f"randstream validate -v --position 500MiB --size 200MiB --expected-checksum 1cb4218e {dev}")
            vm.disconnect_vdi(vdi)
            vm.host.xe('vdi-export', {'uuid': vdi.uuid, 'filename': image_path, 'format': image_format})
            vdi = vdi.destroy()
            # check that the zero blocks are not part of the result
            size_mb = int(vm.host.ssh(f'du -sm --apparent-size {image_path}').split()[0])
            if image_format == 'vhd':
                logging.warning(f"FIXME: this is broken with vhd, skip for now (XCPNG-2631). File size is {size_mb}MB")
            else:
                assert 400 < size_mb < 410, f"unexpected image size: {size_mb}"
            vdi = sr.create_vdi(None, image_format=image_format)
            vm.host.xe('vdi-import', {'uuid': vdi.uuid, 'filename': image_path, 'format': image_format})
            vm.connect_vdi(vdi, 'xvdb')
            vm.ssh(f"randstream validate -v --size 200MiB --expected-checksum c6310c52 {dev}")
            vm.ssh(f"randstream validate -v --position 500MiB --size 200MiB --expected-checksum 1cb4218e {dev}")
        finally:
            if vdi is not None:
                vm.disconnect_vdi(vdi)
                vdi.destroy()
            vm.host.ssh(f'rm -f {image_path}')

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
