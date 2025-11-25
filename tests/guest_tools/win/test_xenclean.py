import pytest

import logging
from pathlib import PureWindowsPath

from lib.common import wait_for
from lib.vm import VM
from lib.windows import (
    WINDOWS_SHUTDOWN_COMMAND,
    check_vm_dns,
    insert_cd_safe,
    set_vm_dns,
    wait_for_vm_running_and_ssh_up_without_tools,
    wait_for_vm_xenvif_offboard,
)

from typing import Any, Dict, Tuple

# Test uninstallation of other drivers using the XenClean program.


def run_xenclean(vm: VM, guest_tools_iso: Dict[str, Any]):
    insert_cd_safe(vm, guest_tools_iso["name"])

    logging.info("Run XenClean")
    xenclean_path = PureWindowsPath("D:\\") / guest_tools_iso["xenclean_path"]
    xenclean_cmd = f"Set-Location C:\\; {xenclean_path} -NoReboot -Confirm:$false; {WINDOWS_SHUTDOWN_COMMAND}"
    vm.start_background_powershell(xenclean_cmd)

    # XenClean sometimes takes a bit long due to all the calls to the uninstallers. We need an extended timeout.
    wait_for(vm.is_halted, "Wait for VM halted", timeout_secs=900)
    vm.eject_cd()

    vm.start()
    wait_for_vm_running_and_ssh_up_without_tools(vm)
    wait_for_vm_xenvif_offboard(vm)


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

        set_vm_dns(vm)
        logging.info("XenClean with test tools")
        run_xenclean(vm, guest_tools_iso)
        logging.info("Check tools uninstalled")
        assert vm.are_windows_tools_uninstalled()
        check_vm_dns(vm)

    def test_xenclean_with_other_tools(self, vm_install_other_drivers: Tuple[VM, Dict], guest_tools_iso):
        vm, param = vm_install_other_drivers
        if param.get("vendor_device"):
            pytest.skip("Skipping XenClean with vendor device present")

        set_vm_dns(vm)
        logging.info("XenClean with other tools")
        run_xenclean(vm, guest_tools_iso)
        logging.info("Check tools uninstalled")
        assert vm.are_windows_tools_uninstalled()
        check_vm_dns(vm)
