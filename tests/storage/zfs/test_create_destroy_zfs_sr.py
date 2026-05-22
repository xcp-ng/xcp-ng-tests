from __future__ import annotations

import pytest

import logging

from lib.common import vm_image
from lib.host import Host
from lib.vdi import ImageFormat

from .conftest import POOL_PATH

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

    def test_create_zfs_sr_without_zfs(self, host: Host, image_format: ImageFormat) -> None:
        # This test must be the first in the series in this module
        assert not host.file_exists('/usr/sbin/zpool'), \
            "zfs must not be installed on the host at the beginning of the tests"
        sr = None
        try:
            sr = host.sr_create('zfs', "ZFS-local-SR-test", {
                'location': POOL_PATH,
                'preferred-image-formats': image_format
            }, verify=True)
        except Exception:
            logging.info("SR creation failed, as expected.")
        if sr is not None:
            sr.destroy()
            assert False, "SR creation should not have succeeded!"

    @pytest.mark.usefixtures("zpool_vol0")
    def test_create_and_destroy_sr(self, host: Host, image_format: ImageFormat) -> None:
        # Create and destroy tested in the same test to leave the host as unchanged as possible
        sr = host.sr_create('zfs', "ZFS-local-SR-test", {
            'location': POOL_PATH,
            'preferred-image-formats': image_format
        }, verify=True)
        # import a VM in order to detect vm import issues here rather than in the vm_on_zfs_sr fixture used in
        # the next tests, because errors in fixtures break teardown
        vm = host.import_vm(vm_image('mini-linux-x86_64-bios'), sr_uuid=sr.uuid)
        vm.destroy(verify=True)
        sr.destroy(verify=True)
