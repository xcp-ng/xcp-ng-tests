import pytest

import logging

from lib.common import vm_image
from lib.host import Host
from lib.pool import Pool

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

    def test_create_cephfs_sr_without_ceph(self, host: Host, cephfs_device_config: dict[str, str]) -> None:
        # This test must be the first in the series in this module
        assert not host.file_exists('/usr/sbin/mount.ceph'), \
            "mount.ceph must not be installed on the host at the beginning of the tests"
        sr = None
        try:
            sr = host.sr_create('cephfs', "CephFS-SR-test", cephfs_device_config, shared=True)
        except Exception:
            logging.info("SR creation failed, as expected.")
        if sr is not None:
            sr.destroy()
            assert False, "SR creation should not have succeeded!"

    def test_create_and_destroy_sr(
        self, host: Host, cephfs_device_config: dict[str, str], pool_with_ceph: Pool
    ) -> None:
        # Create and destroy tested in the same test to leave the host as unchanged as possible
        sr = host.sr_create('cephfs', "CephFS-SR-test", cephfs_device_config, shared=True, verify=True)
        # import a VM in order to detect vm import issues here rather than in the vm_on_cephfs_sr fixture used in
        # the next tests, because errors in fixtures break teardown
        vm = host.import_vm(vm_image('mini-linux-x86_64-bios'), sr_uuid=sr.uuid)
        vm.destroy(verify=True)
        sr.destroy(verify=True)
