import pytest
from lib.common import SSHCommandFailed, wait_for, vm_image

# Requirements:
# - one XCP-ng host >= 8.2 with an additional unused disk for the SR
# - access to XCP-ng RPM repository from the host

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
            sr = host.sr_create('glusterfs', "GlusterFS-SR", glusterfs_device_config, shared=True)
        except Exception:
            print("SR creation failed, as expected.")
        if sr is not None:
            sr.destroy()
            assert False, "SR creation should not have succeeded!"

    def test_create_and_destroy_sr(self, host, pool_with_glusterfs, gluster_volume_started, glusterfs_device_config):
        # Create and destroy tested in the same test to leave the host as unchanged as possible
        sr = host.sr_create('glusterfs', "GlusterFS-SR", glusterfs_device_config, shared=True, verify=True)
        # import a VM in order to detect vm import issues here rather than in the vm_on_glusterfs_fixture used in
        # the next tests, because errors in fixtures break teardown
        vm = host.import_vm(vm_image('mini-linux-x86_64-bios'), sr_uuid=sr.uuid)
        vm.destroy(verify=True)
        sr.destroy(verify=True)

@pytest.mark.usefixtures("glusterfs_sr", "vm_on_glusterfs_sr")
class TestGlusterFSSR:
    def test_start_and_shutdown_VM(self, vm_on_glusterfs_sr):
        vm = vm_on_glusterfs_sr
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    def test_snapshot(self, vm_on_glusterfs_sr):
        vm = vm_on_glusterfs_sr
        vm.start()
        vm.wait_for_os_booted()
        vm.test_snapshot_on_running_vm()
        vm.shutdown(verify=True)

    # *** tests with reboots (longer tests).

    def test_reboot(self, host, glusterfs_sr, vm_on_glusterfs_sr):
        sr = glusterfs_sr
        vm = vm_on_glusterfs_sr
        host.reboot(verify=True)
        wait_for(sr.all_pbds_attached, "Wait for PDB attached")
        # start the VM as a way to check that the underlying SR is operational
        vm.start()
        vm.wait_for_os_booted()
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
                print("SR scan failed as expected.")
            host.ssh(['gluster', '--mode=script', 'volume', 'start', 'vol0'])
            volume_running = True
            sr.plug_pbds(verify=True)
            sr.scan()
        finally:
            if not volume_running:
                host.ssh(['gluster', '--mode=script', 'volume', 'start', 'vol0'])

    # *** End of tests with reboots
