import pytest

import os

from lib.host import Host
from lib.sr import SR
from lib.vm import VM

from .conftest import (
    check_iso_mount_and_read_from_vm,
    copy_tools_iso_to_iso_sr,
    remove_iso_from_sr,
)

# Requirements:
# From --hosts parameter:
# - host: a XCP-ng host, with:
#   - the default SR being either a shared SR, or a local SR on the master host
#   - an additional unused disk for the SR
# From --vm parameter:
# - A VM to import

@pytest.mark.small_vm
@pytest.mark.usefixtures("local_iso_sr")
class TestLocalISOSR:
    @pytest.mark.quicktest
    def test_quicktest(self, local_iso_sr: tuple[SR, str]) -> None:
        sr, _ = local_iso_sr
        sr.run_quicktest()

    def test_iso_mount_and_read(self, host: Host, local_iso_sr: tuple[SR, str], unix_vm: VM) -> None:
        sr, location = local_iso_sr
        iso_path = copy_tools_iso_to_iso_sr(host, sr, location)
        unix_vm.start(on=host.uuid)
        unix_vm.wait_for_vm_running_and_ssh_up()
        try:
            check_iso_mount_and_read_from_vm(host, os.path.basename(iso_path), unix_vm)
        finally:
            # SR cleaning
            remove_iso_from_sr(host, sr, iso_path)
