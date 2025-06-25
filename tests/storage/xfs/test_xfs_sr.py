from __future__ import annotations

import pytest

import logging
import time

from lib.commands import SSHCommandFailed
from lib.common import vm_image, wait_for
from tests.storage import vdi_is_open

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.host import Host

# Requirements:
# - one XCP-ng host >= 8.2 with an additional unused disk for the SR
# - access to XCP-ng RPM repository from the host

class TestXFSSRCreateDestroy:
    """
    Tests that do not use fixtures that setup the SR or import VMs,
    because they precisely need to test SR creation and destruction,
    and VM import.
    """

    def test_create_xfs_sr_without_xfsprogs(self,
                                            host: Host,
                                            unused_512B_disks: dict[Host, list[Host.BlockDeviceInfo]]
                                            ) -> None:
        # This test must be the first in the series in this module
        assert not host.file_exists('/usr/sbin/mkfs.xfs'), \
            "xfsprogs must not be installed on the host at the beginning of the tests"
        sr_disk = unused_512B_disks[host][0]["name"]
        sr = None
        try:
            sr = host.sr_create('xfs', "XFS-local-SR-test", {'device': '/dev/' + sr_disk})
        except Exception:
            logging.info("SR creation failed, as expected.")
        if sr is not None:
            sr.destroy()
            assert False, "SR creation should not have succeeded!"

    def test_create_and_destroy_sr(self,
                                   unused_512B_disks: dict[Host, list[Host.BlockDeviceInfo]],
                                   host_with_xfsprogs: Host
                                   ) -> None:
        # Create and destroy tested in the same test to leave the host as unchanged as possible
        host = host_with_xfsprogs
        sr_disk = unused_512B_disks[host][0]["name"]
        sr = host.sr_create('xfs', "XFS-local-SR-test", {'device': '/dev/' + sr_disk}, verify=True)
        # import a VM in order to detect vm import issues here rather than in the vm_on_xfs fixture used in
        # the next tests, because errors in fixtures break teardown
        vm = host.import_vm(vm_image('mini-linux-x86_64-bios'), sr_uuid=sr.uuid)
        vm.destroy(verify=True)
        sr.destroy(verify=True)

@pytest.mark.usefixtures("xfs_sr")
class TestXFSSR:
    @pytest.mark.quicktest
    def test_quicktest(self, xfs_sr):
        xfs_sr.run_quicktest()

    def test_vdi_is_not_open(self, vdi_on_xfs_sr):
        assert not vdi_is_open(vdi_on_xfs_sr)

    @pytest.mark.small_vm # run with a small VM to test the features
    @pytest.mark.big_vm # and ideally with a big VM to test it scales
    def test_start_and_shutdown_VM(self, vm_on_xfs_sr):
        vm = vm_on_xfs_sr
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_snapshot(self, vm_on_xfs_sr):
        vm = vm_on_xfs_sr
        vm.start()
        try:
            vm.wait_for_os_booted()
            vm.test_snapshot_on_running_vm()
        finally:
            vm.shutdown(verify=True)

    # *** tests with reboots (longer tests).

    @pytest.mark.reboot
    @pytest.mark.small_vm
    def test_reboot(self, vm_on_xfs_sr, host, xfs_sr):
        sr = xfs_sr
        vm = vm_on_xfs_sr
        host.reboot(verify=True)
        wait_for(sr.all_pbds_attached, "Wait for PBD attached")
        # start the VM as a way to check that the underlying SR is operational
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    @pytest.mark.reboot
    def test_xfsprogs_missing(self, host, xfs_sr):
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
