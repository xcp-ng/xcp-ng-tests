import logging
import time
import pytest

from lib.commands import SSHCommandFailed
from lib.common import wait_for, vm_image

# Requirements:
# - one XCP-ng host >= 8.2
# - running MooseFS cluster
# - access to MooseFS packages repository: ppa.moosefs.com

class TestMooseFSSRCreateDestroy:
    """
    Tests that do not use fixtures that setup the SR or import VMs,
    because they precisely need to test SR creation and destruction,
    and VM import.
    """

    def test_create_moosefs_sr_without_mfsmount(self, host, moosefs_device_config):
        # This test must be the first in the series in this module
        assert not host.file_exists('/usr/sbin/mount.moosefs'), \
            "MooseFS client should not be installed on the host"
        sr = None
        try:
            sr = host.sr_create('moosefs', "MooseFS-SR-test1", moosefs_device_config, shared=True)
        except Exception:
            logging.info("MooseFS SR creation failed, as expected.")
        if sr is not None:
            sr.destroy()
            assert False, "MooseFS SR creation should failed!"

    def test_create_and_destroy_sr(self, pool_with_moosefs, moosefs_device_config):
        # Create and destroy tested in the same test to leave the host as unchanged as possible
        master = pool_with_moosefs.master
        sr = master.sr_create('moosefs', "MooseFS-SR-test2", moosefs_device_config, shared=True, verify=True)
        # import a VM in order to detect vm import issues here rather than in the vm_on_moosefs_sr used in
        # the next tests, because errors in fixtures break teardown
        vm = master.import_vm(vm_image('mini-linux-x86_64-bios'), sr_uuid=sr.uuid)
        vm.destroy(verify=True)
        sr.destroy(verify=True)

@pytest.mark.usefixtures("moosefs_sr")
class TestMooseFSSR:
    @pytest.mark.small_vm # run with a small VM to test the features
    @pytest.mark.big_vm # and ideally with a big VM to test it scales
    def test_start_and_shutdown_VM(self, vm_on_moosefs_sr):
        vm = vm_on_moosefs_sr
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_snapshot(self, vm_on_moosefs_sr):
        vm = vm_on_moosefs_sr
        vm.start()
        vm.wait_for_os_booted()
        vm.test_snapshot_on_running_vm()
        vm.shutdown(verify=True)

    # *** tests with reboots (longer tests).

    @pytest.mark.reboot
    @pytest.mark.small_vm
    def test_reboot(self, vm_on_moosefs_sr, host, moosefs_sr):
        sr = moosefs_sr
        vm = vm_on_moosefs_sr
        host.reboot(verify=True)
        wait_for(sr.all_pbds_attached, "Wait for PDB attached")
        # start the VM as a way to check that the underlying SR is operational
        vm.start(on=host.uuid)
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    @pytest.mark.reboot # reboots the host
    def test_moosefs_missing(self, host, moosefs_sr):
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
            host.reboot(verify=True)
            # give the host some time to try to attach the SR
            time.sleep(10)
            logging.info("Assert PBD not attached")
            assert not sr.all_pbds_attached()
            host.yum_install(['moosefs-client'])
            moosefs_installed = True
            sr.plug_pbds(verify=True)
            sr.scan()
        finally:
            if not moosefs_installed:
                host.yum_install(['moosefs-client'])

    # *** End of tests with reboots
