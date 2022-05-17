import logging
import pytest

from lib.commands import SSHCommandFailed
from lib.common import wait_for, vm_image

# Requirements:
# - one XCP-ng host >= 8.2 with an additional unused disk for the SR
# - access to XCP-ng RPM repository from the host

@pytest.mark.usefixtures("sr_disk_for_all_hosts") # don't even run the tests if there's no free disk
class TestGlusterFSSRCreateDestroy:
    """
    Tests that do not use fixtures that setup the SR or import VMs,
    because they precisely need to test SR creation and destruction,
    and VM import.
    """

    def test_create_glusterfs_sr_without_gluster(self, host, glusterfs_device_config):
        # This test must be the first in the series in this module
        assert not host.file_exists('/usr/sbin/glusterd'), \
            "glusterd must not be installed on the host at the beginning of the tests"
        sr = None
        try:
            sr = host.sr_create('glusterfs', "GlusterFS-SR-test", glusterfs_device_config, shared=True)
        except Exception:
            logging.info("SR creation failed, as expected.")
        if sr is not None:
            sr.destroy()
            assert False, "SR creation should not have succeeded!"

    def test_create_and_destroy_sr(self, host, glusterfs_device_config, pool_with_glusterfs, gluster_volume_started):
        # Create and destroy tested in the same test to leave the host as unchanged as possible
        sr = host.sr_create('glusterfs', "GlusterFS-SR-test", glusterfs_device_config, shared=True, verify=True)
        # import a VM in order to detect vm import issues here rather than in the vm_on_glusterfs_fixture used in
        # the next tests, because errors in fixtures break teardown
        vm = host.import_vm(vm_image('mini-linux-x86_64-bios'), sr_uuid=sr.uuid)
        vm.destroy(verify=True)
        sr.destroy(verify=True)

@pytest.mark.usefixtures("sr_disk_for_all_hosts", "glusterfs_sr")
class TestGlusterFSSR:
    @pytest.mark.small_vm # run with a small VM to test the features
    @pytest.mark.big_vm # and ideally with a big VM to test it scales
    def test_start_and_shutdown_VM(self, vm_on_glusterfs_sr):
        vm = vm_on_glusterfs_sr
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_snapshot(self, vm_on_glusterfs_sr):
        vm = vm_on_glusterfs_sr
        vm.start()
        vm.wait_for_os_booted()
        vm.test_snapshot_on_running_vm()
        vm.shutdown(verify=True)

    def test_volume_stopped(self, host, glusterfs_sr):
        sr = glusterfs_sr
        volume_running = True
        try:
            host.ssh(['gluster', '--mode=script', 'volume', 'stop', 'vol0'])
            volume_running = False
            try:
                sr.scan()
                assert False, "SR scan should have failed"
            except SSHCommandFailed:
                logging.info("SR scan failed as expected.")
            host.ssh(['gluster', '--mode=script', 'volume', 'start', 'vol0'])
            volume_running = True
            sr.plug_pbds(verify=True)
            sr.scan()
        finally:
            if not volume_running:
                host.ssh(['gluster', '--mode=script', 'volume', 'start', 'vol0'])

    # *** tests with reboots (longer tests).

    @pytest.mark.reboot
    @pytest.mark.small_vm
    @pytest.mark.flaky # sometimes SR doesn't come back up after reboot
    def test_reboot(self, vm_on_glusterfs_sr, host, glusterfs_sr):
        sr = glusterfs_sr
        vm = vm_on_glusterfs_sr
        host.reboot(verify=True)
        wait_for(sr.all_pbds_attached, "Wait for PDB attached")
        # start the VM as a way to check that the underlying SR is operational
        vm.start(on=host.uuid)
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    # *** End of tests with reboots
