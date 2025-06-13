import logging
from pathlib import PureWindowsPath
from typing import Any, Dict, Tuple

import pytest

from lib.common import wait_for
from lib.vm import VM

from . import WINDOWS_SHUTDOWN_COMMAND, insert_cd_safe, wait_for_vm_running_and_ssh_up_without_tools

def run_xenclean(vm: VM, guest_tools_iso: Dict[str, Any]):
    insert_cd_safe(vm, guest_tools_iso["name"])

    logging.info("Run XenClean")
    xenclean_path = PureWindowsPath("D:\\") / guest_tools_iso["xenclean_path"]
    xenclean_cmd = f"Set-Location C:\\; {xenclean_path} -NoReboot -Confirm:$false; {WINDOWS_SHUTDOWN_COMMAND}"
    vm.start_background_powershell(xenclean_cmd)

    wait_for(vm.is_halted, "Wait for VM halted")
    vm.eject_cd()

    vm.start()
    wait_for_vm_running_and_ssh_up_without_tools(vm)


@pytest.mark.multi_vms
@pytest.mark.usefixtures("windows_vm")
class TestXenClean:
    def test_xenclean_without_tools(self, running_unsealed_windows_vm: VM, guest_tools_iso):
        vm = running_unsealed_windows_vm
        logging.info("XenClean with empty VM")
        run_xenclean(vm, guest_tools_iso)
        assert vm.are_windows_tools_uninstalled()

    def test_xenclean_with_test_tools_early(self, vm_install_test_tools_no_reboot: VM, guest_tools_iso):
        vm = vm_install_test_tools_no_reboot
        logging.info("XenClean with test tools (without reboot)")
        run_xenclean(vm, guest_tools_iso)
        assert vm.are_windows_tools_uninstalled()

    def test_xenclean_with_test_tools(self, vm_install_test_tools_no_reboot: VM, guest_tools_iso):
        vm = vm_install_test_tools_no_reboot
        vm.reboot()
        # HACK: In some cases, vm.reboot(verify=False) followed by vm.insert_cd() (as called by run_xenclean)
        # may cause the VM to hang at the BIOS screen; wait for VM start to avoid this issue.
        wait_for_vm_running_and_ssh_up_without_tools(vm)
        logging.info("XenClean with test tools")
        run_xenclean(vm, guest_tools_iso)
        assert vm.are_windows_tools_uninstalled()

    def test_xenclean_with_other_tools(self, vm_install_other_drivers: Tuple[VM, Dict], guest_tools_iso):
        vm, param = vm_install_other_drivers
        if param.get("vendor_device"):
            pytest.skip("Skipping XenClean with vendor device present")
            return
        logging.info(f"XenClean with other tools")
        run_xenclean(vm, guest_tools_iso)
        assert vm.are_windows_tools_uninstalled()
