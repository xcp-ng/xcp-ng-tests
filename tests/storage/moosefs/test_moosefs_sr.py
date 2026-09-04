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
from tests.storage.storage import check_critical_journal_revert, check_vdi_revert, check_vdi_revert_journal

# Requirements:
# - one XCP-ng host >= 8.2
# - running MooseFS cluster
# - access to MooseFS packages repository: ppa.moosefs.com

# MooseFS doesn't support IPv6
@pytest.mark.usefixtures("moosefs_sr", "host_no_ipv6")
class TestMooseFSSR:
    @pytest.mark.quicktest
    def test_quicktest(self, moosefs_sr: SR) -> None:
        moosefs_sr.run_quicktest()

    def test_vdi_is_not_open(self, vdi_on_moosefs_sr: VDI) -> None:
        assert not vdi_is_open(vdi_on_moosefs_sr)

    @pytest.mark.small_vm # run with a small VM to test the features
    @pytest.mark.big_vm # and ideally with a big VM to test it scales
    def test_start_and_shutdown_VM(self, vm_on_moosefs_sr: VM) -> None:
        vm = vm_on_moosefs_sr
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_snapshot(self, vm_on_moosefs_sr: VM) -> None:
        vm = vm_on_moosefs_sr
        vm.start()
        try:
            vm.wait_for_os_booted()
            vm.test_snapshot_on_running_vm()
        finally:
            vm.shutdown(verify=True)

    def test_moosefs_missing_client_scan_fails(self, host: Host, moosefs_sr: SR) -> None:
        sr = moosefs_sr
        moosefs_installed = True
        try:
            host.yum_remove(['moosefs-client'])
            moosefs_installed = False
            try:
                sr.scan()
                assert False, "SR scan should have failed"
            except SSHCommandFailed:
                logging.info("SR scan failed as expected.")
        finally:
            if not moosefs_installed:
                host.yum_install(['moosefs-client'])

    def test_moosefs_missing_client_pbd_plug_fails(self, host: Host, moosefs_sr: SR) -> None:
        sr = moosefs_sr
        pbd_uuid = sr.pbd_for_host(host)
        moosefs_installed = True
        try:
            sr.unplug_pbd(pbd_uuid)
            host.yum_remove(['moosefs-client'])
            moosefs_installed = False
            try:
                sr.plug_pbd(pbd_uuid)
                assert False, "PBD plug should have failed"
            except SSHCommandFailed:
                logging.info("PBD plug failed as expected.")
            host.yum_install(['moosefs-client'])
            moosefs_installed = True
            sr.plug_pbd(pbd_uuid)
            sr.scan()
        finally:
            if not moosefs_installed:
                host.yum_install(['moosefs-client'])

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_revert(self, vm_on_moosefs_sr: VM, defer: Defer) -> None:
        check_vdi_revert(defer, vm_on_moosefs_sr)

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
    def test_revert_journal(self, vm_on_moosefs_sr: VM, defer: Defer, exit_on_fistpoint: None, fistpoint: str):
        check_vdi_revert_journal(defer, vm_on_moosefs_sr, fistpoint, vm_on_moosefs_sr.host.pool.master)

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_critical_journal_revert(
        self, vm_on_moosefs_sr: VM, defer: Defer, exit_on_fistpoint: None, hostA2: Host
    ) -> None:
        check_critical_journal_revert(defer, vm_on_moosefs_sr, hostA2, "FileSR_revert_create_src")

    # *** tests with reboots (longer tests).

    @pytest.mark.reboot
    @pytest.mark.small_vm
    def test_reboot(self, vm_on_moosefs_sr: VM, host: Host, moosefs_sr: SR) -> None:
        sr = moosefs_sr
        vm = vm_on_moosefs_sr
        host.reboot(verify=True)
        wait_for(sr.all_pbds_attached, "Wait for PBD attached")
        # start the VM as a way to check that the underlying SR is operational
        vm.start(on=host.uuid)
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    # *** End of tests with reboots
