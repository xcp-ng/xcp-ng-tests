"""
Tests demonstrating serial console logging for VMs.
"""

import pytest

from lib.unixvm import UnixVM

@pytest.mark.small_vm
@pytest.mark.usefixtures("serial_console_logger_session")
class TestWithSessionLogging:
    def test_vm_start_stop(self, unix_vm_with_serial_console: UnixVM):
        vm = unix_vm_with_serial_console

        if not vm.is_running():
            vm.start()
        else:
            vm.reboot()

        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    def test_vm_reboots(self, unix_vm_with_serial_console: UnixVM):
        """ test domid tracking in case of reboot """
        vm = unix_vm_with_serial_console

        if not vm.is_running():
            vm.start()

        vm.wait_for_os_booted()

        # First reboot
        vm.reboot(verify=True)
        vm.wait_for_os_booted()

        # Second reboot
        vm.reboot(verify=True)
        vm.wait_for_os_booted()

        vm.shutdown(verify=True)


@pytest.mark.small_vm
@pytest.mark.usefixtures("serial_console_logger_class")
class TestWithClassLogging:
    def test_suspend_resume(self, running_vm: UnixVM):
        running_vm.suspend(verify=True)
        running_vm.resume()
        running_vm.wait_for_os_booted()
