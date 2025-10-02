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

class TestEXTSRCreateDestroy:
    """
    Tests that do not use fixtures that setup the SR or import VMs,
    because they precisely need to test SR creation and destruction,
    and VM import.
    """

    def test_create_sr_with_missing_device(self, host):
        try_to_create_sr_with_missing_device('ext', 'EXT-local-SR-test', host)

    def test_create_and_destroy_sr(self, host: Host, unused_512B_disks: dict[Host, list[Host.BlockDeviceInfo]]) -> None:
        # Create and destroy tested in the same test to leave the host as unchanged as possible
        sr_disk = unused_512B_disks[host][0]["name"]
        sr = host.sr_create('ext', "EXT-local-SR-test", {'device': '/dev/' + sr_disk}, verify=True)
        # import a VM in order to detect vm import issues here rather than in the vm_on_xfs_fixture used in
        # the next tests, because errors in fixtures break teardown
        vm = host.import_vm(vm_image('mini-linux-x86_64-bios'), sr_uuid=sr.uuid)
        vm.destroy(verify=True)
        sr.destroy(verify=True)

@pytest.mark.usefixtures("ext_sr")
class TestEXTSR:
    @pytest.mark.quicktest
    def test_quicktest(self, ext_sr):
        ext_sr.run_quicktest()

    def test_vdi_is_not_open(self, vdi_on_ext_sr):
        assert not vdi_is_open(vdi_on_ext_sr)

    @pytest.mark.small_vm # run with a small VM to test the features
    @pytest.mark.big_vm # and ideally with a big VM to test it scales
    def test_start_and_shutdown_VM(self, vm_on_ext_sr):
        vm = vm_on_ext_sr
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_snapshot(self, vm_on_ext_sr):
        vm = vm_on_ext_sr
        vm.start()
        try:
            vm.wait_for_os_booted()
            vm.test_snapshot_on_running_vm()
        finally:
            vm.shutdown(verify=True)

    @pytest.mark.small_vm
    @pytest.mark.parametrize("vdi_op", ["snapshot", "clone"])
    def test_coalesce(self, unix_vm: 'VM', vdi_on_ext_sr: 'VDI', vdi_op):
        vm = unix_vm
        vdi = vdi_on_ext_sr
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

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_blktap_activate_failure(self, vm_on_ext_sr):
        from lib.fistpoint import FistPoint
        vm = vm_on_ext_sr
        with FistPoint(vm.host, "blktap_activate_inject_failure"), pytest.raises(SSHCommandFailed):
            vm.start()
            vm.shutdown(force=True)

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_resize(self, vm_on_ext_sr):
        vm = vm_on_ext_sr
        vdi = VDI(vm.vdi_uuids()[0], host=vm.host)
        old_size = vdi.get_virtual_size()
        new_size = old_size + (1 * 1024 * 1024 * 1024) # Adding a 1GiB to size

        vdi.resize(new_size)

        assert vdi.get_virtual_size() == new_size

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_failing_resize(self, host, ext_sr, vm_on_ext_sr, exit_on_fistpoint):
        vm = vm_on_ext_sr
        vdi = VDI(vm.vdi_uuids()[0], host=vm.host)
        old_size = vdi.get_virtual_size()
        new_size = old_size + (1 * 1024 * 1024 * 1024) # Adding a 1GiB to size

        with FistPoint(vm.host, "LVHDRT_inflate_after_setSize"):
            try:
                vdi.resize(new_size)
            except SSHCommandFailed:
                logging.info(f"Launching SR scan for {ext_sr} after failure")
                host.xe("sr-scan", {"uuid": ext_sr})

        assert vdi.get_virtual_size() == new_size

    @pytest.mark.reboot
    @pytest.mark.small_vm
    def test_reboot(self, host, ext_sr, vm_on_ext_sr):
        sr = ext_sr
        vm = vm_on_ext_sr
        host.reboot(verify=True)
        wait_for(sr.all_pbds_attached, "Wait for PBD attached")
        # start the VM as a way to check that the underlying SR is operational
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    # *** End of tests with reboots
