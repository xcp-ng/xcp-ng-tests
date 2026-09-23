import pytest

import logging

from lib.commands import SSHCommandFailed
from lib.vm import VM
from lib.windows import (
    PowerAction,
    check_vm_dns,
    set_vm_dns,
    vm_shutdown_without_tools,
    wait_for_vm_running_and_ssh_up_without_tools,
)
from lib.windows.guest_tools import ERROR_INSTALL_FAILURE, install_guest_tools, uninstall_guest_tools

from typing import Any, Tuple

# Requirements:
# - Same as TestGuestToolsWindowsNondestructive.


@pytest.mark.multi_vms
@pytest.mark.usefixtures("windows_vm")
class TestGuestToolsWindowsDestructive:
    def test_uninstall_tools(self, vm_install_test_tools_no_reboot: VM) -> None:
        vm = vm_install_test_tools_no_reboot
        vm_shutdown_without_tools(vm)
        vm.start()
        wait_for_vm_running_and_ssh_up_without_tools(vm)

        set_vm_dns(vm)
        logging.info("Uninstall Windows PV drivers")
        uninstall_guest_tools(vm, action=PowerAction.Reboot)
        logging.info("Check tools uninstalled")
        assert vm.are_windows_tools_uninstalled()
        check_vm_dns(vm)

    def test_uninstall_tools_early(self, vm_install_test_tools_no_reboot: VM) -> None:
        vm = vm_install_test_tools_no_reboot
        logging.info("Uninstall Windows PV drivers before rebooting")
        uninstall_guest_tools(vm, action=PowerAction.Reboot)
        assert vm.are_windows_tools_uninstalled()

    def test_install_with_other_tools(
        self, vm_install_other_drivers: Tuple[VM, dict[str, Any]], guest_tools_iso: dict[str, Any]
    ) -> None:
        vm, param = vm_install_other_drivers
        if param["upgradable"]:
            install_guest_tools(vm, guest_tools_iso, PowerAction.Reboot, check=False)
            assert vm.are_windows_tools_working()
        else:
            exitcode = install_guest_tools(vm, guest_tools_iso, PowerAction.Nothing, check=False)
            assert exitcode == ERROR_INSTALL_FAILURE

    @pytest.mark.usefixtures("uefi_vm")
    def test_uefi_vm_suspend_refused_without_tools(self, running_unsealed_windows_vm: VM) -> None:
        vm = running_unsealed_windows_vm
        with pytest.raises(SSHCommandFailed, match="lacks the feature"):
            vm.suspend()
        wait_for_vm_running_and_ssh_up_without_tools(vm)

    # Test of the unplug rework, where the driver must remain activated even if the device ID changes.
    # Also serves as a "close-enough" test of vendor device toggling.
    def test_toggle_device_id(self, running_unsealed_windows_vm: VM, guest_tools_iso: dict[str, Any]) -> None:
        vm = running_unsealed_windows_vm
        assert vm.param_get("platform", "device_id") == "0002"
        install_guest_tools(vm, guest_tools_iso, PowerAction.Shutdown, check=False)
        vm.param_set("platform", "0001", "device_id")
        vm.start()
        vm.wait_for_vm_running_and_ssh_up()
