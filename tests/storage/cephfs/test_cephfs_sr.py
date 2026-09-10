import pytest

import logging
import time

from lib.commands import SSHCommandFailed
from lib.common import Defer, vm_image, wait_for
from lib.host import Host
from lib.pool import Pool
from lib.sr import SR
from lib.vdi import VDI
from lib.vm import VM
from tests.storage import vdi_is_open
from tests.storage.storage import (
    check_critical_journal_revert,
    check_vdi_revert,
    check_vdi_revert_journal,
)

# Requirements:
# - one XCP-ng host >= 8.2
# - remote cephfs mountpoint
# - access to XCP-ng RPM repository from the host

@pytest.mark.usefixtures("cephfs_sr")
class TestCephFSSR:
    @pytest.mark.quicktest
    def test_quicktest(self, cephfs_sr: SR) -> None:
        cephfs_sr.run_quicktest()

    def test_vdi_is_not_open(self, vdi_on_cephfs_sr: VDI) -> None:
        assert not vdi_is_open(vdi_on_cephfs_sr)

    @pytest.mark.small_vm # run with a small VM to test the features
    @pytest.mark.big_vm # and ideally with a big VM to test it scales
    def test_start_and_shutdown_VM(self, vm_on_cephfs_sr: VM) -> None:
        vm = vm_on_cephfs_sr
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_snapshot(self, vm_on_cephfs_sr: VM) -> None:
        vm = vm_on_cephfs_sr
        vm.start()
        try:
            vm.wait_for_os_booted()
            vm.test_snapshot_on_running_vm()
        finally:
            vm.shutdown(verify=True)

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_revert(self, vm_on_cephfs_sr: VM, defer: Defer) -> None:
        check_vdi_revert(defer, vm_on_cephfs_sr)

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
    def test_revert_journal(self, vm_on_cephfs_sr: VM, defer: Defer, exit_on_fistpoint: None, fistpoint: str):
        check_vdi_revert_journal(defer, vm_on_cephfs_sr, fistpoint, vm_on_cephfs_sr.host.pool.master)

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_critical_journal_revert(
        self, vm_on_cephfs_sr: VM, defer: Defer, exit_on_fistpoint: None, hostA2: Host
    ) -> None:
        check_critical_journal_revert(defer, vm_on_cephfs_sr, hostA2, "FileSR_revert_create_src")

    # *** tests with reboots (longer tests).

    @pytest.mark.reboot
    @pytest.mark.small_vm
    def test_reboot(self, vm_on_cephfs_sr: VM, host: Host, cephfs_sr: SR) -> None:
        sr = cephfs_sr
        vm = vm_on_cephfs_sr
        host.reboot(verify=True)
        wait_for(sr.all_pbds_attached, "Wait for PBD attached")
        # start the VM as a way to check that the underlying SR is operational
        vm.start(on=host.uuid)
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    @pytest.mark.reboot # reboots the host
    def test_ceph_missing(self, host: Host, cephfs_sr: SR) -> None:
        sr = cephfs_sr
        ceph_installed = True
        try:
            host.yum_remove(['ceph-common'])
            ceph_installed = False
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
            host.yum_install(['ceph-common'])
            ceph_installed = True
            sr.plug_pbds(verify=True)
            sr.scan()
        finally:
            if not ceph_installed:
                host.yum_install(['ceph-common'])

    # *** End of tests with reboots
