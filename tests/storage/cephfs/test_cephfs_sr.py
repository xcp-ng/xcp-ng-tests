import pytest
from lib.common import wait_for, vm_image
import time
import subprocess

# Requirements:
# - one XCP-ng host >= 8.2
# - remote cephfs mountpoint
# - access to XCP-ng RPM repository from the host

class TestCephFSSRCreateDestroy:
    """
    Tests that do not use fixtures that setup the SR or import VMs,
    because they precisely need to test SR creation and destruction,
    and VM import.
    """

    def test_create_cephfs_sr_without_ceph(self, host, cephfs_device_config):
        # This test must be the first in the series in this module
        assert not host.file_exists('/usr/sbin/mount.ceph'), \
            "mount.ceph must not be installed on the host at the beginning of the tests"
        sr = None
        try:
            sr = host.sr_create('cephfs', "CephFS-SR", cephfs_device_config, shared=True)
        except Exception:
            print("SR creation failed, as expected.")
        if sr is not None:
            sr.destroy()
            assert False, "SR creation should not have succeeded!"

    def test_create_and_destroy_sr(self, host, pool_with_ceph, cephfs_device_config):
        # Create and destroy tested in the same test to leave the host as unchanged as possible
        sr = host.sr_create('cephfs', "CephFS-SR", cephfs_device_config, shared=True, verify=True)
        # import a VM in order to detect vm import issues here rather than in the vm_on_xfs_fixture used in
        # the next tests, because errors in fixtures break teardown
        vm = host.import_vm(vm_image('mini-linux-x86_64-bios'), sr_uuid=sr.uuid)
        vm.destroy(verify=True)
        sr.destroy(verify=True)


@pytest.mark.usefixtures("cephfs_sr", "vm_on_cephfs_sr")
class TestCephFSSR:
    def test_start_and_shutdown_VM(self, vm_on_cephfs_sr):
        vm = vm_on_cephfs_sr
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    def test_snapshot(self, vm_on_cephfs_sr):
        vm = vm_on_cephfs_sr
        vm.start()
        vm.wait_for_os_booted()
        vm.test_snapshot_on_running_vm()
        vm.shutdown(verify=True)

    # *** tests with reboots (longer tests).

    def test_reboot(self, host, cephfs_sr, vm_on_cephfs_sr):
        sr = cephfs_sr
        vm = vm_on_cephfs_sr
        host.reboot(verify=True)
        wait_for(sr.all_pbds_attached, "Wait for PDB attached")
        # start the VM as a way to check that the underlying SR is operational
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    def test_ceph_missing(self, host, cephfs_sr):
        sr = cephfs_sr
        ceph_installed = True
        try:
            host.yum_remove(['ceph-common'])
            ceph_installed = False
            try:
                sr.scan()
                assert False, "SR scan should have failed"
            except subprocess.CalledProcessError:
                print("SR scan failed as expected.")
            host.reboot(verify=True)
            # give the host some time to try to attach the SR
            time.sleep(10)
            print("Assert PBD not attached")
            assert not sr.all_pbds_attached()
            host.yum_install(['ceph-common'])
            ceph_installed = True
            sr.plug_pbds(verify=True)
            sr.scan()
        finally:
            if not ceph_installed:
                host.yum_install(['ceph-common'])

    # *** End of tests with reboots
