import pytest

import logging
import time

from lib.commands import SSHCommandFailed
from lib.common import strtobool, wait_for
from lib.vm import VM

from . import WINDOWS_SHUTDOWN_COMMAND, PowerAction, wait_for_vm_running_and_ssh_up_without_tools
from .guest_tools import (
    ERROR_INSTALL_FAILURE,
    install_guest_tools,
    uninstall_guest_tools,
)

from typing import Any, Tuple

# Requirements:
# - XCP-ng >= 8.2.
#
# From --vm parameter:
# - A Windows VM with the following requirements:
#   - User "root" present and has admin privileges
#   - Xen PV tools not installed
#   - Reports its IP via serial console on boot in this format:
#     "~xcp-ng-tests~<mac>=<ip>~end~\r\n"
#     The VM should report its IP frequently since the test script acquires the VM's IP based on a timeout.
#   - Git Bash
#   - OpenSSH:
#     - Server enabled and allowed by firewall
#     - SSH key installed into %ProgramData%\ssh\administrators_authorized_keys with appropriate permissions
#     - Registry value "HKLM\SOFTWARE\OpenSSH DefaultShell" set to the full path of Git Bash's bash.exe
#
# Specific configuration:
# - ISO image of guest tools under test following the structure in data.WIN_GUEST_TOOLS_ISOS, e.g.:
#   guest-tools-win.iso
#   ├───package
#   │   ├───XenDrivers-x64.msi
#   │   └───XenClean
#   └───testsign
#       └───*.crt
# - ISO image of other guest tools following the structure in data.OTHER_GUEST_TOOLS, e.g.:
#   other-guest-tools-win.iso
#   ├───citrix-9.4.0
#   │   ├───XenBus
#   │   ├───XenIface
#   │   ├───XenNet
#   │   ├───XenVbd
#   │   ├───XenVif
#   │   └───managementagent-9.4.0-x64.msi
#   ├───xcp-ng-8.2.2.200
#   │   └───managementagentx64.msi
#   ├───xcp-ng-9.0.9000
#   │   ├───package
#   │   │   └───XenDrivers-x64.msi
#   │   └───testsign
#   │       └───*.crt
#   └───install-drivers.ps1


@pytest.mark.multi_vms
@pytest.mark.usefixtures("windows_vm")
class TestGuestToolsWindows:
    def test_drivers_detected(self, vm_install_test_tools_per_test_class: VM):
        pass

    def test_vif_replug(self, vm_install_test_tools_per_test_class: VM):
        vm = vm_install_test_tools_per_test_class
        vifs = vm.vifs()
        for vif in vifs:
            assert strtobool(vif.param_get("currently-attached"))
            vif.unplug()
            # HACK: Allow some time for the unplug to settle. If not, Windows guests have a tendency to explode.
            assert not strtobool(vif.param_get("currently-attached"))
            time.sleep(5)
            vif.plug()
        wait_for(vm.is_ssh_up, "Wait for SSH up")


@pytest.mark.multi_vms
@pytest.mark.usefixtures("windows_vm")
class TestGuestToolsWindowsDestructive:
    def test_uninstall_tools(self, vm_install_test_tools_no_reboot: VM):
        vm = vm_install_test_tools_no_reboot
        vm.ssh(WINDOWS_SHUTDOWN_COMMAND)
        wait_for(vm.is_halted, "Shutdown VM")

        vm.start()
        wait_for_vm_running_and_ssh_up_without_tools(vm)
        logging.info("Uninstall Windows PV drivers")
        uninstall_guest_tools(vm, action=PowerAction.Reboot)
        assert vm.are_windows_tools_uninstalled()

    def test_uninstall_tools_early(self, vm_install_test_tools_no_reboot: VM):
        vm = vm_install_test_tools_no_reboot
        logging.info("Uninstall Windows PV drivers before rebooting")
        uninstall_guest_tools(vm, action=PowerAction.Reboot)
        assert vm.are_windows_tools_uninstalled()

    def test_install_with_other_tools(
        self, vm_install_other_drivers: Tuple[VM, dict[str, Any]], guest_tools_iso: dict[str, Any]
    ):
        vm, param = vm_install_other_drivers
        if param["upgradable"]:
            install_guest_tools(vm, guest_tools_iso, PowerAction.Reboot, check=False)
            assert vm.are_windows_tools_working()
        else:
            exitcode = install_guest_tools(vm, guest_tools_iso, PowerAction.Nothing, check=False)
            assert exitcode == ERROR_INSTALL_FAILURE

    @pytest.mark.usefixtures("uefi_vm")
    def test_uefi_vm_suspend_refused_without_tools(self, running_unsealed_windows_vm: VM):
        vm = running_unsealed_windows_vm
        with pytest.raises(SSHCommandFailed, match="lacks the feature"):
            vm.suspend()
        wait_for_vm_running_and_ssh_up_without_tools(vm)

    # Test of the unplug rework, where the driver must remain activated even if the device ID changes.
    # Also serves as a "close-enough" test of vendor device toggling.
    def test_toggle_device_id(self, running_unsealed_windows_vm: VM, guest_tools_iso: dict[str, Any]):
        vm = running_unsealed_windows_vm
        assert vm.param_get("platform", "device_id") == "0002"
        install_guest_tools(vm, guest_tools_iso, PowerAction.Shutdown, check=False)
        vm.param_set("platform", "0001", "device_id")
        vm.start()
        vm.wait_for_vm_running_and_ssh_up()
