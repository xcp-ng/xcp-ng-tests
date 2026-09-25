from __future__ import annotations

import pytest

import logging

from lib.common import vm_image
from lib.host import Host
from lib.sr import SR
from lib.vdi import ImageFormat

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
                                            unused_512B_disks: dict[Host, list[Host.BlockDeviceInfo]],
                                            image_format: ImageFormat
                                            ) -> None:
        # This test must be the first in the series in this module
        assert not host.file_exists('/usr/sbin/mkfs.xfs'), \
            "xfsprogs must not be installed on the host at the beginning of the tests"
        sr_disk = unused_512B_disks[host][0].name
        sr = None
        try:
            sr = host.sr_create('xfs', "XFS-local-SR-test", {
                'device': '/dev/' + sr_disk,
                'preferred-image-formats': image_format
            })
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
        sr_disk = unused_512B_disks[host][0].name
        sr = host.sr_create('xfs', "XFS-local-SR-test", {'device': '/dev/' + sr_disk}, verify=True)
        # import a VM in order to detect vm import issues here rather than in the vm_on_xfs fixture used in
        # the next tests, because errors in fixtures break teardown
        vm = host.import_vm(vm_image('mini-linux-x86_64-bios'), sr_uuid=sr.uuid)
        vm.destroy(verify=True)
        sr.destroy(verify=True)
