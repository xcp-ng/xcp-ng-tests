import pytest

import logging

from lib.commands import SSHCommandFailed
from lib.common import vm_image, wait_for
from lib.host import Host
from lib.pool import Pool
from lib.sr import SR
from lib.vdi import VDI
from lib.vm import VM
from tests.storage import vdi_is_open

# Requirements:
# - one XCP-ng host >= 8.2 with an additional unused disk for the SR
# - access to XCP-ng RPM repository from the host

class TestGlusterFSSR:
    @pytest.mark.quicktest
    def test_quicktest(self, glusterfs_sr: SR) -> None:
        glusterfs_sr.run_quicktest()

    def test_vdi_is_not_open(self, vdi_on_glusterfs_sr: VDI) -> None:
        assert not vdi_is_open(vdi_on_glusterfs_sr)

    @pytest.mark.small_vm # run with a small VM to test the features
    @pytest.mark.big_vm # and ideally with a big VM to test it scales
    def test_start_and_shutdown_VM(self, vm_on_glusterfs_sr: VM) -> None:
        vm = vm_on_glusterfs_sr
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_snapshot(self, vm_on_glusterfs_sr: VM) -> None:
        vm = vm_on_glusterfs_sr
        vm.start()
        try:
            vm.wait_for_os_booted()
            vm.test_snapshot_on_running_vm()
        finally:
            vm.shutdown(verify=True)

    def test_volume_stopped(self, host: Host, glusterfs_sr: SR) -> None:
        sr = glusterfs_sr
        volume_running = True
        try:
            host.ssh('gluster --mode=script volume stop vol0')
            volume_running = False
            try:
                sr.scan()
                assert False, "SR scan should have failed"
            except SSHCommandFailed:
                logging.info("SR scan failed as expected.")
            host.ssh('gluster --mode=script volume start vol0')
            volume_running = True
            sr.plug_pbds(verify=True)
            sr.scan()
        finally:
            if not volume_running:
                host.ssh('gluster --mode=script volume start vol0')

    # *** tests with reboots (longer tests).

    @pytest.mark.reboot
    @pytest.mark.small_vm
    @pytest.mark.flaky # sometimes SR doesn't come back up after reboot
    def test_reboot(self, vm_on_glusterfs_sr: VM, host: Host, glusterfs_sr: SR) -> None:
        sr = glusterfs_sr
        vm = vm_on_glusterfs_sr
        host.reboot(verify=True)
        wait_for(sr.all_pbds_attached, "Wait for PBD attached")
        # start the VM as a way to check that the underlying SR is operational
        vm.start(on=host.uuid)
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    # *** End of tests with reboots
