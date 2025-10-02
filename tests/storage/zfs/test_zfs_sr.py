import pytest

import logging
import time

from lib.commands import SSHCommandFailed
from lib.common import vm_image, wait_for
from tests.storage import vdi_is_open
from tests.storage.storage import install_randstream, operation_on_vdi, wait_for_vdi_coalesce

from .conftest import POOL_NAME, POOL_PATH

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.vdi import VDI
    from lib.vm import VM

# Requirements:
# - one XCP-ng host >= 8.2 with an additional unused disk for the SR
# - access to XCP-ng RPM repository from the host

@pytest.mark.usefixtures("sr_disk_wiped")
class TestZFSSRCreateDestroy:
    """
    Tests that do not use fixtures that setup the SR or import VMs,
    because they precisely need to test SR creation and destruction,
    and VM import.
    """

    def test_create_zfs_sr_without_zfs(self, host):
        # This test must be the first in the series in this module
        assert not host.file_exists('/usr/sbin/zpool'), \
            "zfs must not be installed on the host at the beginning of the tests"
        sr = None
        try:
            sr = host.sr_create('zfs', "ZFS-local-SR-test", {'location': POOL_PATH})
        except Exception:
            logging.info("SR creation failed, as expected.")
        if sr is not None:
            sr.destroy()
            assert False, "SR creation should not have succeeded!"

    @pytest.mark.usefixtures("zpool_vol0")
    def test_create_and_destroy_sr(self, host):
        # Create and destroy tested in the same test to leave the host as unchanged as possible
        sr = host.sr_create('zfs', "ZFS-local-SR-test", {'location': POOL_PATH}, verify=True)
        # import a VM in order to detect vm import issues here rather than in the vm_on_xfs_fixture used in
        # the next tests, because errors in fixtures break teardown
        vm = host.import_vm(vm_image('mini-linux-x86_64-bios'), sr_uuid=sr.uuid)
        vm.destroy(verify=True)
        sr.destroy(verify=True)

@pytest.mark.usefixtures("zpool_vol0")
class TestZFSSR:
    @pytest.mark.quicktest
    def test_quicktest(self, zfs_sr):
        zfs_sr.run_quicktest()

    def test_vdi_is_not_open(self, vdi_on_zfs_sr):
        assert not vdi_is_open(vdi_on_zfs_sr)

    @pytest.mark.small_vm # run with a small VM to test the features
    @pytest.mark.big_vm # and ideally with a big VM to test it scales
    def test_start_and_shutdown_VM(self, vm_on_zfs_sr):
        vm = vm_on_zfs_sr
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_snapshot(self, vm_on_zfs_sr):
        vm = vm_on_zfs_sr
        vm.start()
        try:
            vm.wait_for_os_booted()
            vm.test_snapshot_on_running_vm()
        finally:
            vm.shutdown(verify=True)

    @pytest.mark.small_vm
    @pytest.mark.parametrize("vdi_op", ["snapshot", "clone"])
    def test_coalesce(self, unix_vm: 'VM', vdi_on_zfs_sr: 'VDI', vdi_op):
        vm = unix_vm
        vdi = vdi_on_zfs_sr
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

    @pytest.mark.reboot
    @pytest.mark.small_vm
    def test_reboot(self, vm_on_zfs_sr, host, zfs_sr):
        sr = zfs_sr
        vm = vm_on_zfs_sr
        host.reboot(verify=True)
        wait_for(sr.all_pbds_attached, "Wait for PBD attached")
        # start the VM as a way to check that the underlying SR is operational
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    @pytest.mark.reboot
    def test_zfs_missing(self, host, zfs_sr):
        sr = zfs_sr
        zfs_installed = True
        try:
            host.yum_remove(['zfs'])
            zfs_installed = False
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
            host.yum_install(['zfs'])
            host.ssh(['modprobe', 'zfs'])
            zfs_installed = True
            host.ssh(['zpool', 'import', POOL_NAME])
            sr.plug_pbds(verify=True)
            sr.scan()
        finally:
            if not zfs_installed:
                host.yum_install(['zfs'])
                host.ssh(['modprobe', 'zfs'])

    @pytest.mark.reboot
    def test_zfs_unmounted(self, host, zfs_sr):
        sr = zfs_sr
        zpool_imported = True
        try:
            # Simulate broken mountpoint
            host.ssh(['zpool', 'export', POOL_NAME])
            zpool_imported = False
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
            host.ssh(['zpool', 'import', POOL_NAME])
            zpool_imported = True
            sr.plug_pbds(verify=True)
            sr.scan()
        finally:
            if not zpool_imported:
                host.ssh(['zpool', 'import', POOL_NAME])

    # *** End of tests with reboots
