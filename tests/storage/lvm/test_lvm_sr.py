from __future__ import annotations

import pytest

import logging

from lib.commands import SSHCommandFailed
from lib.common import vm_image, wait_for
from lib.fistpoint import FistPoint
from tests.storage import try_to_create_sr_with_missing_device, vdi_is_open
from tests.storage.storage import install_randstream, operation_on_vdi, wait_for_vdi_coalesce

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.host import Host
    from lib.vdi import VDI
    from lib.vm import VM

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

    def test_create_and_destroy_sr(self, host: Host, unused_512B_disks: dict[Host, list[Host.BlockDeviceInfo]]) -> None:
        sr_disk = unused_512B_disks[host][0]["name"]
        # Create and destroy tested in the same test to leave the host as unchanged as possible
        sr = host.sr_create('lvm', "LVM-local-SR-test", {'device': '/dev/' + sr_disk}, verify=True)
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
    def test_coalesce(self, unix_vm: 'VM', vdi_on_lvm_sr: 'VDI', vdi_op):
        vm = unix_vm
        vdi = vdi_on_lvm_sr
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
