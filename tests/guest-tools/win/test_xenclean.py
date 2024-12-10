import logging
from pathlib import PureWindowsPath
from typing import Any
import pytest
from lib.common import wait_for
from lib.vm import VM

from . import wait_for_vm_running_and_ssh_up_without_tools


def run_xenclean(vm: VM, guest_tools_iso: dict[str, Any]):
    vm.insert_cd(guest_tools_iso["name"])
    wait_for(lambda: vm.file_exists("D:/", regular_file=False))

    logging.info("Run XenClean")
    xenclean_path = PureWindowsPath("D:\\") / guest_tools_iso["xenclean_path"]
    xenclean_cmd = f"Set-Location C:\\; {xenclean_path} -NoReboot -Confirm:$false; Stop-Computer -Force"
    vm.start_background_powershell(xenclean_cmd)

    wait_for(vm.is_halted, "Wait for VM halted")
    vm.eject_cd()

    vm.start()
    wait_for_vm_running_and_ssh_up_without_tools(vm)


@pytest.mark.multi_vms
@pytest.mark.usefixtures("windows_vm")
class TestXenClean:
    def test_xenclean_without_tools(self, running_unsealed_windows_vm, guest_tools_iso):
        vm = running_unsealed_windows_vm
        logging.info("XenClean with empty VM")
        run_xenclean(vm, guest_tools_iso)
        assert vm.are_windows_tools_uninstalled()

    def test_xenclean_with_test_tools(self, vm_install_test_tools, guest_tools_iso):
        vm = vm_install_test_tools
        logging.info("XenClean with test tools")
        run_xenclean(vm, guest_tools_iso)
        assert vm.are_windows_tools_uninstalled()

    def test_xenclean_with_other_tools(self, vm_install_other_drivers, guest_tools_iso):
        vm, _ = vm_install_other_drivers
        logging.info(f"XenClean with other tools")
        run_xenclean(vm, guest_tools_iso)
        assert vm.are_windows_tools_uninstalled()
