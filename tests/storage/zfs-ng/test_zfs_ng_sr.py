import logging
import time
import pytest

from conftest import POOL_PATH
from lib.commands import SSHCommandFailed
from lib.common import wait_for, vm_image
from tests.storage import vdi_is_open

# Requirements:
# - one XCP-ng host >= 8.2 with an additional unused disk for the SR
# - access to XCP-ng RPM repository from the host

# @pytest.mark.usefixtures("sr_disk_wiped")
class TestZFSNGSRCreateDestroy:
    """
    Tests that do not use fixtures that setup the SR or import VMs,
    because they precisely need to test SR creation and destruction,
    and VM import.
    """

    """
    TODO: to uncomment this in the future
    def test_create_zfs_sr_without_zfs(self, host):
        # This test must be the first in the series in this module
        assert not host.file_exists('/usr/sbin/zpool'), \
            "zfs must not be installed on the host at the beginning of the tests"
        sr = None
        try:
            sr = host.sr_create('zfs', "ZFS-local-SR-test", {'location': VOLUME_PATH})
        except Exception:
            logging.info("SR creation failed, as expected.")
        if sr is not None:
            sr.destroy()
            assert False, "SR creation should not have succeeded!"
    """

#   @pytest.mark.usefixtures("zpool_vol0")
    # This tests default mode without any parameter
    def test_create_and_destroy_sr(self, host):
        # Create and destroy tested in the same test to leave the host as unchanged as possible
        # TODO: this is failing because it is adding [matiasv] in the label for the name of the SR
        sr = host.sr_create('zfs-ng', "ZFS-ng-local-SR-test", {'devices': 'sdb'}, verify=True)

        # TODO: not sure if this check is necessary
        vols = host.ssh(['zpool', 'list', '-Ho', 'name']).splitlines()

        if not 'ZFS-ng-local-SR-test' in vols:
            assert False, "SR is not in list"

        sr.destroy(verify=True)

    def test_create_and_destroy_sr_w_compression(self, host):
        sr = host.sr_create('zfs-ng', "ZFS-ng-local-SR-test", {'devices': 'sdb', 'compression': 'on'}, verify=True)
        # TODO: to check if compression is on on that volume
        is_comp = host.ssh(['zfs', 'get', '-H', '-o', 'value', 'compression', 'ZFS-ng-local-SR-test'])

        if not 'on' in is_comp:
            assert False, "Compression has not been set"

        sr.destroy(verify=True)

    def test_create_and_destroy_sr_wo_compression(self, host):
        sr = host.sr_create('zfs-ng', "ZFS-ng-local-SR-test", {'devices': 'sdb', 'compression': 'off'}, verify=True)
        # TODO: to check if compression is on on that volume
        is_comp = host.ssh(['zfs', 'get', '-H', '-o', 'value', 'compression', 'ZFS-ng-local-SR-test'])

        if not 'off' in is_comp:
            assert False, "Compression has not been set"

        sr.destroy(verify=True)

    def test_create_and_destroy_sr_w_mountpoint(self, host):
        sr = host.sr_create('zfs-ng', "ZFS-ng-local-SR-test", {'devices': 'sdb', 'mountpoint': POOL_PATH}, verify=True)
        mountpoint = host.ssh(['zfs', 'get', '-H', '-o', 'value', 'mountpoint', 'ZFS-ng-local-SR-test'])

        if not mountpoint == POOL_PATH:
            assert False, "SR was not created in the right mountpoint"

        sr.destroy(verify=True)

    def test_create_and_destroy_sr_mirror(self, host):
        sr = host.sr_create('zfs-ng', "ZFS-ng-local-SR-test", {'devices': 'sdb,sdc', 'mode': 'M'}, verify=True)
        sr.destroy(verify=True)

    def test_create_and_destroy_sr_raidz(self, host):
        sr = host.sr_create('zfs-ng', "ZFS-ng-local-SR-test", {'devices': 'sdb,sdc', 'mode': 'R'}, verify=True)
        sr.destroy(verify=True)

    def test_create_and_destroy_sr_w_vm(self, host):
        sr = host.sr_create('zfs-ng', "ZFS-ng-local-SR-test", {'devices': 'sdb'}, verify=True)
        # import a VM in order to detect vm import issues here rather than in the vm_on_xfs_fixture used in
        # the next tests, because errors in fixtures break teardown
        vm = host.import_vm(vm_image('mini-linux-x86_64-bios'), sr_uuid=sr.uuid)
        vm.destroy(verify=True)
        sr.destroy(verify=True)

class TestZFSNGVDI:
    #def test_vdi_is_not_open(self, vdi_on_zfsng_sr):
    #    assert not vdi_is_open(vdi_on_zfsng_sr)

    @pytest.mark.small_vm
    def test_snapshot(self, vm_on_zfs_sr):
        vm = vm_on_zfs_sr
        vm.start()
        vm.wait_for_os_booted()
        vm.test_snapshot_on_running_vm()
        vm.shutdown(verify=True)

    @pytest.mark.small_vm
    def test_start_and_shutdown_VM(self, vm_on_zfs_sr):
        vm = vm_on_zfs_sr
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    @pytest.mark.small_vm
    def test_reboot(self, vm_on_zfs_sr, host, zfsng_sr):
        sr = zfsng_sr
        vm = vm_on_zfs_sr
        host.reboot(verify=True, reconnect_xo=False)
        wait_for(sr.all_pbds_attached, "Wait for PBD attached")
        # start the VM as a way to check that the underlying SR is operational
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)
