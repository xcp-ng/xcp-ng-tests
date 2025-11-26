from __future__ import annotations

import pytest

import logging
import time

from lib.commands import SSHCommandFailed
from lib.common import vm_image, wait_for
from lib.host import Host
from lib.vdi import VDI
from lib.vm import VM
from tests.storage import vdi_is_open
from tests.storage.storage import install_randstream

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

    @pytest.mark.small_vm
    @pytest.mark.parametrize("vdi_op", ["snapshot", "clone"])
    def test_coalesce(self, storage_test_vm: VM, vdi_on_xfs_sr: VDI, vdi_op):
        vm = storage_test_vm
        vdi = vdi_on_xfs_sr
        vm.connect_vdi(vdi, 'xvdb')
        new_vdi = None
        try:
            vm.ssh("randstream generate -v /dev/xvdb")
            vm.ssh("randstream validate -v --expected-checksum 65280014 /dev/xvdb")
            match vdi_op:
                case 'clone': new_vdi = vdi.clone()
                case 'snapshot': new_vdi = vdi.snapshot()
                case _: raise pytest.fail(f"unexpected vdi operation: {vdi_op}")
            vm.ssh("randstream generate -v --seed 1 --size 128Mi /dev/xvdb")
            vm.ssh("randstream validate -v --expected-checksum ad2ca9af /dev/xvdb")
            new_vdi.destroy()
            new_vdi = None
            vdi.wait_for_coalesce()
            vm.ssh("randstream validate -v --expected-checksum ad2ca9af /dev/xvdb")
        finally:
            vm.disconnect_vdi(vdi)
            if new_vdi is not None:
                new_vdi.destroy()

    @pytest.mark.small_vm
    @pytest.mark.parametrize("compression", ["none", "gzip", "zstd"])
    def test_xva_export_import(self, vm_on_xfs_sr: VM, compression):
        vm = vm_on_xfs_sr
        vm.start()
        vm.wait_for_vm_running_and_ssh_up()
        install_randstream(vm)
        # 500MiB, so we have some data to check and some empty spaces in the exported image
        vm.ssh("randstream generate -v --size 500MiB /root/data")
        vm.ssh("randstream validate -v --expected-checksum 24e905d6 /root/data")
        vm.shutdown(verify=True)
        xva_path = f'/tmp/{vm.uuid}.xva'
        imported_vm = None
        try:
            vm.export(xva_path, compression)
            # check that the zero blocks are not part of the result. Most of the data is from the random stream, so
            # compression has little effect. We just check the result is between 500 and 700 MiB
            size_mb = int(vm.host.ssh(f'du -sm {xva_path}').split()[0])
            assert 500 < size_mb < 700, f"unexpected xva size: {size_mb}"
            imported_vm = vm.host.import_vm(xva_path, vm.vdis[0].sr.uuid)
            imported_vm.start()
            imported_vm.wait_for_vm_running_and_ssh_up()
            imported_vm.ssh("randstream validate -v --expected-checksum 24e905d6 /root/data")
        finally:
            if imported_vm is not None:
                imported_vm.destroy()
            vm.host.ssh(f'rm -f {xva_path}')

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
