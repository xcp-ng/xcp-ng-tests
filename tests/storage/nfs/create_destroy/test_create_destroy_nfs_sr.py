from __future__ import annotations

import pytest

from lib.common import vm_image
from lib.host import Host

# Requirements:
# - one XCP-ng host >= 8.0 with an additional unused disk for the SR

class TestNFSSRCreateDestroy:
    @pytest.mark.parametrize('dispatch_nfs', ['nfs_device_config', 'nfs4_device_config'], indirect=True)
    def test_create_and_destroy_sr(self, host: Host, dispatch_nfs: dict[str, str]) -> None:
        device_config = dispatch_nfs
        # Create and destroy tested in the same test to leave the host as unchanged as possible
        sr = host.sr_create('nfs', "NFS-SR-test", device_config, shared=True, verify=True)
        # import a VM in order to detect vm import issues here rather than in the vm_on_nfs fixture used in
        # the next tests, because errors in fixtures break teardown
        vm = host.import_vm(vm_image('mini-linux-x86_64-bios'), sr_uuid=sr.uuid)
        vm.destroy(verify=True)
        sr.destroy(verify=True)
