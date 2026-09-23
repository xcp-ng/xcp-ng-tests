from __future__ import annotations

import pytest

from lib.common import vm_image
from lib.host import Host
from lib.vdi import ImageFormat

# Requirements:
# - one XCP-ng host >= 8.2
# - a valid lvmoiscsi config

class TestLVMOISCSISRCreateDestroy:
    """
    Tests that do not use fixtures that setup the SR or import VMs,
    because they precisely need to test SR creation and destruction,
    and VM import.
    """

    def test_create_and_destroy_sr(self, host: Host, lvmoiscsi_device_config: dict[str, str],
                                   image_format: ImageFormat) -> None:
        # Create and destroy tested in the same test to leave the host as unchanged as possible
        sr = host.sr_create('lvmoiscsi', "lvmoiscsi-SR-test",
                            lvmoiscsi_device_config | {'preferred-image-formats': image_format},
                            shared=True, verify=True)
        # import a VM in order to detect vm import issues here rather than in the vm_on_xfs_fixture used in
        # the next tests, because errors in fixtures break teardown
        vm = host.import_vm(vm_image('mini-linux-x86_64-bios'), sr_uuid=sr.uuid)
        vm.destroy(verify=True)
        sr.destroy(verify=True)
