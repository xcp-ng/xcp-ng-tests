from __future__ import annotations

import pytest

from lib.commands import SSHCommandFailed
from lib.common import vm_image, wait_for
from lib.sr import SR
from lib.vdi import VDI
from lib.vm import VM
from tests.storage import (
    CoalesceOperation,
    ImageFormat,
    XVACompression,
    coalesce_integrity,
    vdi_export_import,
    vdi_is_open,
    xva_export_import,
)

# Requirements:
# - one XCP-ng host >= 8.0 with an additional unused disk for the SR

class TestNFSSRCreateDestroy:
    @pytest.mark.parametrize('dispatch_nfs', ['nfs_device_config', 'nfs4_device_config'], indirect=True)
    def test_create_and_destroy_sr(self, host, dispatch_nfs):
        device_config = dispatch_nfs
        # Create and destroy tested in the same test to leave the host as unchanged as possible
        sr = host.sr_create('nfs', "NFS-SR-test", device_config, shared=True, verify=True)
        # import a VM in order to detect vm import issues here rather than in the vm_on_nfs fixture used in
        # the next tests, because errors in fixtures break teardown
        vm = host.import_vm(vm_image('mini-linux-x86_64-bios'), sr_uuid=sr.uuid)
        vm.destroy(verify=True)
        sr.destroy(verify=True)

# Make sure this fixture is called before the parametrized one
@pytest.mark.usefixtures('image_format')
class TestNFSSR:
    @pytest.mark.quicktest
    @pytest.mark.parametrize('dispatch_nfs', ['nfs_sr', 'nfs4_sr'], indirect=True)
    def test_quicktest(self, dispatch_nfs):
        sr = dispatch_nfs
        sr.run_quicktest()

    @pytest.mark.parametrize('dispatch_nfs', ['vdi_on_nfs_sr', 'vdi_on_nfs4_sr'], indirect=True)
    def test_vdi_is_not_open(self, dispatch_nfs):
        vdi = dispatch_nfs
        assert not vdi_is_open(vdi)

    @pytest.mark.small_vm
    @pytest.mark.usefixtures('hostA2')
    # Make sure this fixture is called before the parametrized one
    @pytest.mark.usefixtures('vm_ref')
    @pytest.mark.parametrize('dispatch_nfs', ['vm_on_nfs_sr', 'vm_on_nfs4_sr'], indirect=True)
    def test_plugin_nfs_on_on_slave(self, dispatch_nfs: VM):
        vm = dispatch_nfs
        vm.start()
        vm.wait_for_os_booted()
        host = vm.get_residence_host()

        vdi = vm.vdis[0]
        image_format = vdi.get_image_format() or "vhd"

        vdi_path = f"/run/sr-mount/{vdi.sr.uuid}/{vdi.uuid}.{image_format}"

        # nfs-on-slave returns an error when the VDI is open on the host.
        # Otherwise, it return "success", including in case "path" doesn't exist
        with pytest.raises(SSHCommandFailed) as excinfo:
            host.call_plugin("nfs-on-slave", "check", {"path": vdi_path})

        # The output of the host plugin would have "stdout: NfsCheckException"
        # and information about which process has the path open.
        assert "NfsCheckException" in excinfo.value.stdout

        for member in host.pool.hosts:
            # skip the host where the VM is running
            if member.uuid == host.uuid:
                continue
            member.call_plugin("nfs-on-slave", "check", {"path": vdi_path})

        vm.shutdown(verify=True)

    @pytest.mark.parametrize('dispatch_nfs', ['vdi_on_nfs_sr', 'vdi_on_nfs4_sr'], indirect=True)
    def test_vdi_image_format(self, dispatch_nfs: VDI, image_format: ImageFormat):
        fmt = dispatch_nfs.get_image_format()
        # feature-detect: if the SM doesn't report image-format, skip this check
        if not fmt:
            pytest.skip("SM does not report sm-config:image-format; skipping format check")
        assert fmt == image_format

    @pytest.mark.small_vm # run with a small VM to test the features
    @pytest.mark.big_vm # and ideally with a big VM to test it scales
    # Make sure this fixture is called before the parametrized one
    @pytest.mark.usefixtures('vm_ref')
    @pytest.mark.parametrize('dispatch_nfs', ['vm_on_nfs_sr', 'vm_on_nfs4_sr'], indirect=True)
    def test_start_and_shutdown_VM(self, dispatch_nfs):
        vm = dispatch_nfs
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    # Make sure this fixture is called before the parametrized one
    @pytest.mark.usefixtures('vm_ref')
    @pytest.mark.parametrize('dispatch_nfs', ['vm_on_nfs_sr', 'vm_on_nfs4_sr'], indirect=True)
    def test_snapshot(self, dispatch_nfs):
        vm = dispatch_nfs
        vm.start()
        try:
            vm.wait_for_os_booted()
            vm.test_snapshot_on_running_vm()
        finally:
            vm.shutdown(verify=True)

    @pytest.mark.small_vm
    @pytest.mark.parametrize('dispatch_nfs', ['vdi_on_nfs_sr', 'vdi_on_nfs4_sr'], indirect=True)
    @pytest.mark.parametrize('vdi_op', ['snapshot', 'clone'])
    def test_coalesce(self, storage_test_vm: VM, dispatch_nfs: VDI, vdi_op: CoalesceOperation):
        coalesce_integrity(storage_test_vm, dispatch_nfs, vdi_op)

    @pytest.mark.small_vm
    # Make sure this fixture is called before the parametrized one
    @pytest.mark.usefixtures('vm_ref')
    @pytest.mark.parametrize('dispatch_nfs', ['vm_on_nfs_sr', 'vm_on_nfs4_sr'], indirect=True)
    @pytest.mark.parametrize("compression", ["none", "gzip", "zstd"])
    def test_xva_export_import(self, dispatch_nfs: VM, compression: XVACompression):
        xva_export_import(dispatch_nfs, compression)

    @pytest.mark.small_vm
    @pytest.mark.parametrize('dispatch_nfs', ['nfs_sr', 'nfs4_sr'], indirect=True)
    def test_vdi_export_import(self, storage_test_vm: VM, dispatch_nfs: SR, image_format: ImageFormat):
        vdi_export_import(storage_test_vm, dispatch_nfs, image_format)

    # *** tests with reboots (longer tests).

    @pytest.mark.reboot
    @pytest.mark.small_vm
    # Make sure this fixture is called before the parametrized one
    @pytest.mark.usefixtures('vm_ref')
    @pytest.mark.parametrize('dispatch_nfs', ['vm_on_nfs_sr', 'vm_on_nfs4_sr'], indirect=True)
    def test_reboot(self, host, dispatch_nfs):
        vm = dispatch_nfs
        sr = vm.get_sr()
        host.reboot(verify=True)
        wait_for(sr.all_pbds_attached, "Wait for PBD attached")
        # start the VM as a way to check that the underlying SR is operational
        vm.start(on=host.uuid)
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    # *** End of tests with reboots
