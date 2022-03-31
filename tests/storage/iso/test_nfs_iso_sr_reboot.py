import pytest
import os
from lib.common import wait_for
from conftest import copy_tools_iso_to_iso_sr, check_iso_mount_and_read_from_vm, remove_iso_from_sr

# Requirements:
# From --hosts parameter:
# - host: a XCP-ng host
# From --vm parameter
# - A VM to import
# From data.py or --sr-device-config parameter: configuration to create a new NFS ISO SR.

@pytest.mark.usefixtures("nfs_iso_sr")
class TestNFSISOSRReboot:
    """
    This test is longer because the host reboots between the creation of the SR and the test with the ISO file.
    """

    def test_iso_mount_and_read_after_reboot(self, host, nfs_iso_sr, unix_vm):
        # create the ISO SR on nfs
        sr = nfs_iso_sr
        iso_path = copy_tools_iso_to_iso_sr(host, sr)
        host.reboot(verify=True)
        wait_for(sr.all_pbds_attached, "Wait for PDB attached")
        unix_vm.start()
        unix_vm.wait_for_os_booted()
        try:
            check_iso_mount_and_read_from_vm(host, os.path.basename(iso_path), unix_vm)
        finally:
            # SR cleaning
            remove_iso_from_sr(host, sr, iso_path)
