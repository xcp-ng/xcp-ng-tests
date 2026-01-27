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

# To keep in sync with win-pvdrivers/XenClean/ExitCode.cs
XENCLEAN_EXIT_CODES = {
    0: "CleaningSucceeded",
    1: "Error",
    2: "UserCanceled",
    64: "ReadyForOnboard",
    65: "AlreadyOnboarded",
    66: "OnboardDenied",
}


def run_xenclean(vm: VM, guest_tools_iso: Dict[str, Any]):
    insert_cd_safe(vm, guest_tools_iso["name"])

    logging.info("Run XenClean")
    xenclean_path = PureWindowsPath("D:\\") / guest_tools_iso["xenclean_path"]
    xenclean_cmd = f"Set-Location C:\\; {xenclean_path} -NoReboot -NoConfirm; {WINDOWS_SHUTDOWN_COMMAND}"
    vm.start_background_powershell(xenclean_cmd)

    # XenClean sometimes takes a bit long due to all the calls to the uninstallers. We need an extended timeout.
    wait_for(vm.is_halted, "Wait for VM halted", timeout_secs=900)
    vm.eject_cd()

    vm.start()
    wait_for_vm_running_and_ssh_up_without_tools(vm)
    wait_for_vm_xenvif_offboard(vm)


def run_xenclean_onboard_dryrun(vm: VM, guest_tools_iso: Dict[str, Any]):
    """
    The "onboard" mode of XenClean removes any existing tools that don't match a given vendor name.
    Its goal is to help users transition from one Xen driver vendor to another.

    Note: Running XenClean for real requires a reboot and causes the virtual network to disconnect, which makes
    harvesting the XenClean exit code for the onboard status more complicated. Since all we want is the exit code, we
    just run it in dry-run mode here.
    """
    insert_cd_safe(vm, guest_tools_iso["name"])

    logging.info("Run XenClean (onboarding dry-run)")
    xenclean_path = PureWindowsPath("D:\\") / guest_tools_iso["xenclean_path"]
    xenclean_cmd = (
        "Set-Location C:\\; "
        f"{xenclean_path} -NoReboot -NoConfirm -Onboard XCP-ng -DryRun > $null; "
        "Write-Output $LASTEXITCODE"
    )
    exitcode = vm.execute_powershell_script(xenclean_cmd)

    vm.eject_cd()
    return int(exitcode)


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

    def test_onboarding_without_tools(self, running_unsealed_windows_vm: VM, guest_tools_iso):
        vm = running_unsealed_windows_vm
        logging.info("XenClean onboarding with empty VM")
        exitcode = run_xenclean_onboard_dryrun(vm, guest_tools_iso)
        assert XENCLEAN_EXIT_CODES.get(exitcode) == "ReadyForOnboard"

    def test_onboarding_with_other_tools(self, vm_install_other_drivers: Tuple[VM, Dict], guest_tools_iso):
        vm, param = vm_install_other_drivers
        expected_phase = param.get("onboarding_phase")
        if not expected_phase:
            pytest.skip("Skipping this guest tool since onboarding phase is not specified")

        logging.info("XenClean onboarding with other tools")
        exitcode = run_xenclean_onboard_dryrun(vm, guest_tools_iso)
        assert XENCLEAN_EXIT_CODES.get(exitcode) == expected_phase
