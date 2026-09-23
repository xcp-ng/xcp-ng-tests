from __future__ import annotations

import pytest

from lib.common import vm_image
from lib.host import Host
from lib.vdi import ImageFormat
from tests.storage import try_to_create_sr_with_missing_device

# Requirements:
# - one XCP-ng host with an additional unused disk for the SR

class TestEXTSRCreateDestroy:
    """
    Tests that do not use fixtures that setup the SR or import VMs,
    because they precisely need to test SR creation and destruction,
    and VM import.
    """

    def test_create_sr_with_missing_device(self, host: Host) -> None:
        try_to_create_sr_with_missing_device('ext', 'EXT-local-SR-test', host)

    def test_create_and_destroy_sr(self, host: Host,
                                   unused_512B_disks: dict[Host, list[Host.BlockDeviceInfo]],
                                   image_format: ImageFormat
                                   ) -> None:
        # Create and destroy tested in the same test to leave the host as unchanged as possible
        sr_disk = unused_512B_disks[host][0].name
        sr = host.sr_create('ext', "EXT-local-SR-test",
                            {'device': '/dev/' + sr_disk,
                             'preferred-image-formats': image_format}, verify=True)
        # import a VM in order to detect vm import issues here rather than in the vm_on_xfs_fixture used in
        # the next tests, because errors in fixtures break teardown
        vm = host.import_vm(vm_image('mini-linux-x86_64-bios'), sr_uuid=sr.uuid)
        vm.destroy(verify=True)
        sr.destroy(verify=True)
