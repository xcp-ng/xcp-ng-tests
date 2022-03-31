import pytest
import os
from lib.common import wait_for
from conftest import copy_tools_iso_to_iso_sr, check_iso_mount_and_read_from_vm, remove_iso_from_sr

# Requirements:
# From --hosts parameter:
# - host: a XCP-ng host
# From --sr-disk parameter:
# - an additional unused disk for the SR
# From --vm parameter:
# - A VM to import

@pytest.mark.usefixtures("local_iso_sr")
class TestLocalISOSRReboot:
    """
    This test is longer because the host reboots between the creation of the SR and the test with the ISO file.
    """

    def test_iso_mount_and_read_after_reboot(self, host, local_iso_sr, unix_vm):
        sr, location = local_iso_sr
        iso_path = copy_tools_iso_to_iso_sr(host, sr, location)
        host.reboot(verify=True)
        wait_for(sr.all_pbds_attached, "Wait for PDB attached")
        unix_vm.start()
        unix_vm.wait_for_os_booted()
        try:
            check_iso_mount_and_read_from_vm(host, os.path.basename(iso_path), unix_vm)
        finally:
            # SR cleaning
            remove_iso_from_sr(host, sr, iso_path)
