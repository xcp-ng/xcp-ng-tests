from __future__ import annotations

import pytest

import logging

from lib.common import vm_image
from lib.host import Host
from lib.pool import Pool

# Requirements:
# - one XCP-ng host >= 8.2
# - running MooseFS cluster
# - access to MooseFS packages repository: ppa.moosefs.com

class TestMooseFSSRCreateDestroy:
    """
    Tests that do not use fixtures that setup the SR or import VMs,
    because they precisely need to test SR creation and destruction,
    and VM import.
    """

    def test_create_moosefs_sr_without_mfsmount(self, host: Host, moosefs_device_config: dict[str, str]) -> None:
        # This test must be the first in the series in this module
        assert not host.file_exists('/usr/sbin/mount.moosefs'), \
            "MooseFS client should not be installed on the host"
        sr = None
        try:
            sr = host.sr_create('moosefs', "MooseFS-SR-test1", moosefs_device_config, shared=True)
        except Exception:
            logging.info("MooseFS SR creation failed, as expected.")
        if sr is not None:
            sr.destroy()
            assert False, "MooseFS SR creation should failed!"

    # MooseFS doesn't support IPv6
    @pytest.mark.usefixtures("host_no_ipv6")
    def test_create_and_destroy_sr(
        self, moosefs_device_config: dict[str, str], pool_with_moosefs_enabled: Pool
    ) -> None:
        # Create and destroy tested in the same test to leave the host as unchanged as possible
        master = pool_with_moosefs_enabled.master
        sr = master.sr_create('moosefs', "MooseFS-SR-test2", moosefs_device_config, shared=True, verify=True)
        # import a VM in order to detect vm import issues here rather than in the vm_on_moosefs_sr used in
        # the next tests, because errors in fixtures break teardown
        vm = master.import_vm(vm_image('mini-linux-x86_64-bios'), sr_uuid=sr.uuid)
        vm.destroy(verify=True)
        sr.destroy(verify=True)
