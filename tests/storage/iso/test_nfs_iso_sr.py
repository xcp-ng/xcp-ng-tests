import pytest

import logging
import os

from lib.host import Host
from lib.sr import SR
from lib.vm import VM

from .conftest import check_iso_mount_and_read_from_vm, copy_tools_iso_to_iso_sr, remove_iso_from_sr

# Requirements:
# From --hosts parameter:
# - host: a XCP-ng host
# From --vm parameter
# - A VM to import
# From data.py or --sr-device-config parameter: configuration to create a new NFS ISO SR.

@pytest.mark.small_vm
@pytest.mark.usefixtures("nfs_iso_sr")
class TestNFSISOSR:
    @pytest.mark.quicktest
    def test_quicktest(self, nfs_iso_sr: SR) -> None:
        nfs_iso_sr.run_quicktest()

    def test_iso_mount_and_read(self, host: Host, nfs_iso_sr: SR, running_unix_vm: VM) -> None:
        # create the ISO SR on NFS
        sr = nfs_iso_sr
        iso_path = copy_tools_iso_to_iso_sr(host, sr)
        try:
            check_iso_mount_and_read_from_vm(host, os.path.basename(iso_path), running_unix_vm)
        finally:
            # SR cleaning
            remove_iso_from_sr(host, sr, iso_path)
