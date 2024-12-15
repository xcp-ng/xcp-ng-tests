import logging
import pytest

from . import PowerAction, wait_for_vm_running_and_ssh_up_without_tools
from .guest_tools import (
    ERROR_INSTALL_FAILURE,
    install_guest_tools,
    uninstall_guest_tools,
)


# Requirements:
# - XCP-ng >= 8.3.
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
    def test_tools_after_reboot(self, vm_install_test_tools_per_test_class):
        vm = vm_install_test_tools_per_test_class
        assert vm.are_windows_tools_working()

    def test_drivers_detected(self, vm_install_test_tools_per_test_class):
        vm = vm_install_test_tools_per_test_class
        assert vm.are_windows_tools_working()


@pytest.mark.multi_vms
@pytest.mark.usefixtures("windows_vm")
class TestGuestToolsWindowsDestructive:
    def test_uninstall_tools(self, vm_install_test_tools):
        vm = vm_install_test_tools
        vm.reboot()
        wait_for_vm_running_and_ssh_up_without_tools(vm)
        logging.info("Uninstall Windows PV drivers")
        uninstall_guest_tools(vm, action=PowerAction.Reboot)
        assert vm.are_windows_tools_uninstalled()

    def test_uninstall_tools_early(self, vm_install_test_tools):
        vm = vm_install_test_tools
        logging.info("Uninstall Windows PV drivers before rebooting")
        uninstall_guest_tools(vm, action=PowerAction.Reboot)
        assert vm.are_windows_tools_uninstalled()

    def test_install_with_other_tools(self, vm_install_other_drivers, guest_tools_iso):
        vm, param = vm_install_other_drivers
        if param["upgradable"]:
            install_guest_tools(vm, guest_tools_iso, PowerAction.Reboot, check=False)
            assert vm.are_windows_tools_working()
        else:
            exitcode = install_guest_tools(vm, guest_tools_iso, PowerAction.Nothing, check=False)
            assert exitcode == ERROR_INSTALL_FAILURE
