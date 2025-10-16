from __future__ import annotations

import pytest

import logging

from lib.commands import SSHCommandFailed
from lib.common import vm_image, wait_for
from lib.fistpoint import FistPoint
from lib.host import Host
from lib.sr import SR
from lib.vdi import VDI
from lib.vm import VM
from tests.storage import (
    try_to_create_sr_with_missing_device,
    vdi_is_open,
)
from tests.storage.storage import install_randstream

# Requirements:
# - one XCP-ng host with an additional unused disk for the SR

class TestLVMSRCreateDestroy:
    """
    Tests that do not use fixtures that setup the SR or import VMs,
    because they precisely need to test SR creation and destruction,
    and VM import.
    """

    def test_create_sr_with_missing_device(self, host):
        try_to_create_sr_with_missing_device('lvm', 'LVM-local-SR-test', host)

    def test_create_and_destroy_sr(self, host: Host,
                                   unused_512B_disks: dict[Host, list[Host.BlockDeviceInfo]],
                                   image_format: str
                                   ) -> None:
        sr_disk = unused_512B_disks[host][0]["name"]
        # Create and destroy tested in the same test to leave the host as unchanged as possible
        sr = host.sr_create('lvm', "LVM-local-SR-test", {
            'device': '/dev/' + sr_disk,
            'preferred-image-formats': image_format
        }, verify=True)
        # import a VM in order to detect vm import issues here rather than in the vm_on_xfs_fixture used in
        # the next tests, because errors in fixtures break teardown
        vm = host.import_vm(vm_image('mini-linux-x86_64-bios'), sr_uuid=sr.uuid)
        vm.destroy(verify=True)
        sr.destroy(verify=True)

@pytest.mark.usefixtures("lvm_sr")
class TestLVMSR:
    @pytest.mark.quicktest
    def test_quicktest(self, lvm_sr):
        lvm_sr.run_quicktest()

    def test_vdi_is_not_open(self, vdi_on_lvm_sr):
        assert not vdi_is_open(vdi_on_lvm_sr)

    def test_vdi_image_format(self, vdi_on_lvm_sr: VDI, image_format: str):
        fmt = vdi_on_lvm_sr.get_image_format()
        # feature-detect: if the SM doesn't report image-format, skip this check
        if not fmt:
            pytest.skip("SM does not report sm-config:image-format; skipping format check")
        assert fmt == image_format

    @pytest.mark.small_vm # run with a small VM to test the features
    @pytest.mark.big_vm # and ideally with a big VM to test it scales
    def test_start_and_shutdown_VM(self, vm_on_lvm_sr):
        vm = vm_on_lvm_sr
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_snapshot(self, vm_on_lvm_sr):
        vm = vm_on_lvm_sr
        vm.start()
        try:
            vm.wait_for_os_booted()
            vm.test_snapshot_on_running_vm()
        finally:
            vm.shutdown(verify=True)

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_failing_resize_on_inflate_after_setSize(self, host, lvm_sr, vm_on_lvm_sr, exit_on_fistpoint):
        vm = vm_on_lvm_sr
        lvinflate = ""
        vdi = VDI(vm.vdi_uuids()[0], host=vm.host)
        new_size = vdi.get_virtual_size() + (1 * 1024 * 1024 * 1024) # Adding a 1GiB to size

        with FistPoint(vm.host, "LVHDRT_inflate_after_setSize"), pytest.raises(SSHCommandFailed) as exc_info:
            vdi.resize(new_size)
        logging.info(exc_info)

        lvlist = host.lvs(f"VG_XenStorage-{lvm_sr.uuid}")
        for lv in lvlist:
            if lv.startswith("inflate_"):
                logging.info(f"Found inflate journal following error: {lv}")
                lvinflate = lv

        logging.info(f"Launching SR scan for {lvm_sr.uuid} to resolve failure")
        try:
            host.xe("sr-scan", {"uuid": lvm_sr.uuid})
        except SSHCommandFailed as e:
            assert False, f"Failing to scan following a inflate error {e}"
        assert lvinflate not in host.lvs(f"VG_XenStorage-{lvm_sr.uuid}"), \
            "Inflate journal still exist following the scan"

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_failing_resize_on_inflate_after_setSizePhys(self, host, lvm_sr, vm_on_lvm_sr, exit_on_fistpoint):
        vm = vm_on_lvm_sr
        lvinflate = ""
        vdi = VDI(vm.vdi_uuids()[0], host=vm.host)
        new_size = vdi.get_virtual_size() + (1 * 1024 * 1024 * 1024) # Adding a 1GiB to size

        with FistPoint(vm.host, "LVHDRT_inflate_after_setSizePhys"), pytest.raises(SSHCommandFailed) as exc_info:
            vdi.resize(new_size)
        logging.info(exc_info)

        lvlist = host.lvs(f"VG_XenStorage-{lvm_sr.uuid}")
        for lv in lvlist:
            if lv.startswith("inflate_"):
                logging.info(f"Found inflate journal following error: {lv}")
                lvinflate = lv

        logging.info(f"Launching SR scan for {lvm_sr.uuid} to resolve failure")
        try:
            host.xe("sr-scan", {"uuid": lvm_sr.uuid})
        except SSHCommandFailed as e:
            assert False, f"Failing to scan following a inflate error {e}"
        assert lvinflate not in host.lvs(f"VG_XenStorage-{lvm_sr.uuid}"), \
            "Inflate journal still exist following the scan"

    @pytest.mark.small_vm
    @pytest.mark.parametrize("vdi_op", ["snapshot", "clone"])
    def test_coalesce(self, storage_test_vm: VM, vdi_on_lvm_sr: VDI, vdi_op):
        vm = storage_test_vm
        vdi = vdi_on_lvm_sr
        vm.connect_vdi(vdi, 'xvdb')
        new_vdi = None
        try:
            vm.ssh("randstream generate -v /dev/xvdb")
            vm.ssh("randstream validate -v --expected-checksum 65280014 /dev/xvdb")
            match vdi_op:
                case 'clone': new_vdi = vdi.clone()
                case 'snapshot': new_vdi = vdi.snapshot()
                case _: raise pytest.fail(f"unexpected vdi operation: {vdi_op}")
            vm.ssh("randstream generate -v --seed 1 --size 128Mi /dev/xvdb")
            vm.ssh("randstream validate -v --expected-checksum ad2ca9af /dev/xvdb")
            new_vdi.destroy()
            new_vdi = None
            vdi.wait_for_coalesce()
            vm.ssh("randstream validate -v --expected-checksum ad2ca9af /dev/xvdb")
        finally:
            vm.disconnect_vdi(vdi)
            if new_vdi is not None:
                new_vdi.destroy()

    @pytest.mark.small_vm
    @pytest.mark.parametrize("compression", ["none", "gzip", "zstd"])
    def test_xva_export_import(self, vm_on_lvm_sr: VM, compression):
        vm = vm_on_lvm_sr
        vm.start()
        vm.wait_for_vm_running_and_ssh_up()
        install_randstream(vm)
        # 500MiB, so we have some data to check and some empty spaces in the exported image
        vm.ssh("randstream generate -v --size 500MiB /root/data")
        vm.ssh("randstream validate -v --expected-checksum 24e905d6 /root/data")
        vm.shutdown(verify=True)
        xva_path = f'/tmp/{vm.uuid}.xva'
        imported_vm = None
        try:
            vm.export(xva_path, compression)
            # check that the zero blocks are not part of the result. Most of the data is from the random stream, so
            # compression has little effect. We just check the result is between 500 and 700 MiB
            size_mb = int(vm.host.ssh(f'du -sm {xva_path}').split()[0])
            assert 500 < size_mb < 700, f"unexpected xva size: {size_mb}"
            imported_vm = vm.host.import_vm(xva_path, vm.vdis[0].sr.uuid)
            imported_vm.start()
            imported_vm.wait_for_vm_running_and_ssh_up()
            imported_vm.ssh("randstream validate -v --expected-checksum 24e905d6 /root/data")
        finally:
            if imported_vm is not None:
                imported_vm.destroy()
            vm.host.ssh(f'rm -f {xva_path}')

    @pytest.mark.small_vm
    def test_vdi_export_import(self, storage_test_vm: VM, lvm_sr: SR, image_format: str):
        vm = storage_test_vm
        sr = lvm_sr
        vdi = sr.create_vdi(image_format=image_format)
        image_path = f'/tmp/{vdi.uuid}.{image_format}'
        try:
            vm.connect_vdi(vdi, 'xvdb')
            # generate 2 blocks of data of 200MiB, at position 0 and at position 500MiB
            vm.ssh("randstream generate -v --size 200MiB /dev/xvdb")
            vm.ssh("randstream generate -v --seed 1 --position 500MiB --size 200MiB /dev/xvdb")
            vm.ssh("randstream validate -v --size 200MiB --expected-checksum c6310c52 /dev/xvdb")
            vm.ssh("randstream validate -v --position 500MiB --size 200MiB --expected-checksum 1cb4218e /dev/xvdb")
            vm.disconnect_vdi(vdi)
            vm.host.xe('vdi-export', {'uuid': vdi.uuid, 'filename': image_path, 'format': image_format})
            vdi = vdi.destroy()
            # check that the zero blocks are not part of the result
            size_mb = int(vm.host.ssh(f'du -sm {image_path}').split()[0])
            assert 400 < size_mb < 410, f"unexpected image size: {size_mb}"
            vdi = sr.create_vdi(image_format=image_format)
            vm.host.xe('vdi-import', {'uuid': vdi.uuid, 'filename': image_path, 'format': image_format})
            vm.connect_vdi(vdi, 'xvdb')
            vm.ssh("randstream validate -v --size 200MiB --expected-checksum c6310c52 /dev/xvdb")
            vm.ssh("randstream validate -v --position 500MiB --size 200MiB --expected-checksum 1cb4218e /dev/xvdb")
        finally:
            if vdi is not None:
                vm.disconnect_vdi(vdi)
                vdi.destroy()
            vm.host.ssh(f'rm -f {image_path}')

    # *** tests with reboots (longer tests).

    @pytest.mark.reboot
    @pytest.mark.small_vm
    def test_reboot(self, host, lvm_sr, vm_on_lvm_sr):
        sr = lvm_sr
        vm = vm_on_lvm_sr
        host.reboot(verify=True)
        wait_for(sr.all_pbds_attached, "Wait for PBD attached")
        # start the VM as a way to check that the underlying SR is operational
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    # *** End of tests with reboots
