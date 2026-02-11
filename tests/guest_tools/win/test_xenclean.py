from __future__ import annotations

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

from typing import Any, Literal, overload

# Test uninstallation of other drivers using the XenClean program.

ONBOARDING_PHASES = {
    0: "CleaningSucceeded",
    1: "Error",
    2: "UserCanceled",
    3: "RebootPending",
    64: "ReadyForOnboard",
    65: "AlreadyOnboarded",
    66: "OnboardDenied",
}

ONBOARD_EXIT_CODE_FILE = "C:\\onboard.txt"


@overload
def run_xenclean(vm: VM, guest_tools_iso: dict[str, Any], onboard: Literal[False] = ...) -> None:  #
    ...


@overload
def run_xenclean(vm: VM, guest_tools_iso: dict[str, Any], onboard: Literal[True]) -> str:  #
    ...


def run_xenclean(vm: VM, guest_tools_iso: dict[str, Any], onboard: bool = False) -> str | None:
    """
    Run XenClean from the provided guest tools.

    A note on guest tools onboarding with XenClean:
    Onboarding is the transition from one guest tool to another, typically driven externally by repeatedly running
    XenClean. XenClean will exit with one of the exit codes documented in ONBOARDING_PHASES.
    """
    insert_cd_safe(vm, guest_tools_iso["name"])

    logging.info("Run XenClean")
    xenclean_path = PureWindowsPath("D:\\") / guest_tools_iso["xenclean_path"]
    if guest_tools_iso["xenclean_path"].lower().endswith(".ps1"):
        assert not onboard, "Onboarding not supported in older versions"
        xenclean_cmd = f"Set-Location C:\\; {xenclean_path} -NoReboot -Confirm:$false; {WINDOWS_SHUTDOWN_COMMAND}"
    else:
        xenclean_cmd = f"Set-Location C:\\; {xenclean_path} -noReboot -noConfirm"
        if onboard:
            onboard_family = guest_tools_iso["onboard_family"]
            xenclean_cmd += f" -onboard {onboard_family}; Set-Content {ONBOARD_EXIT_CODE_FILE} $LASTEXITCODE -Force"
        else:
            xenclean_cmd += "; if ($LASTEXITCODE -ne 0) {{throw}}"
        xenclean_cmd += f"; {WINDOWS_SHUTDOWN_COMMAND}"
    vm.start_background_powershell(xenclean_cmd)

    # XenClean sometimes takes a bit long due to all the calls to the uninstallers. We need an extended timeout.
    wait_for(vm.is_halted, "Wait for VM halted", timeout_secs=900)
    vm.eject_cd()

    vm.start()
    wait_for_vm_running_and_ssh_up_without_tools(vm)
    wait_for_vm_xenvif_offboard(vm)
    if onboard:
        exitcode = vm.execute_powershell_script(f"Get-Content {ONBOARD_EXIT_CODE_FILE} -ErrorAction SilentlyContinue")
        logging.debug(f"Onboarding exit code: {exitcode}")
        assert exitcode, "Expected exit code string"
        onboarding_phase = ONBOARDING_PHASES[int(exitcode)]
        logging.info(f"Onboarding phase: {onboarding_phase}")
        return onboarding_phase
    else:
        return None


@pytest.fixture(scope="module")
def onboarding_guest_tools_iso(guest_tools_iso: dict[str, Any]) -> dict[str, Any]:
    if not guest_tools_iso.get("onboard_family"):
        pytest.skip("Onboarding info not declared in data.py")
    return guest_tools_iso


@pytest.mark.multi_vms
@pytest.mark.usefixtures("windows_vm")
class TestXenClean:
    def test_xenclean_without_tools(
        self, running_unsealed_windows_vm: VM, guest_tools_iso: dict[str, Any]
    ) -> None:
        vm = running_unsealed_windows_vm
        logging.info("XenClean with empty VM")
        run_xenclean(vm, guest_tools_iso)
        assert vm.are_windows_tools_uninstalled()

    def test_xenclean_onboard_without_tools(self, running_unsealed_windows_vm: VM,
                                            onboarding_guest_tools_iso: dict[str, Any]) -> None:
        vm = running_unsealed_windows_vm
        logging.info("XenClean onboard with empty VM")
        assert run_xenclean(vm, onboarding_guest_tools_iso, onboard=True) == "ReadyForOnboard"

    def test_xenclean_with_test_tools_early(
        self, vm_install_test_tools_no_reboot: VM, guest_tools_iso: dict[str, Any]
    ) -> None:
        vm = vm_install_test_tools_no_reboot
        logging.info("XenClean with test tools (without reboot)")
        run_xenclean(vm, guest_tools_iso)
        assert vm.are_windows_tools_uninstalled()

    def test_xenclean_with_test_tools(self, vm_install_test_tools_no_reboot: VM,
                                      guest_tools_iso: dict[str, Any]) -> None:
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

    def test_xenclean_onboard_with_test_tools(self, vm_install_test_tools_no_reboot: VM,
                                              onboarding_guest_tools_iso: dict[str, Any]) -> None:
        vm = vm_install_test_tools_no_reboot
        vm.reboot()
        wait_for_vm_running_and_ssh_up_without_tools(vm)

        logging.info("XenClean onboard with test tools")
        assert run_xenclean(vm, onboarding_guest_tools_iso, onboard=True) == "AlreadyOnboarded"
        logging.info("Check tools still working")
        assert vm.are_windows_tools_working()

    def test_xenclean_with_other_tools(
        self, vm_install_other_drivers: tuple[VM, dict[str, Any]], guest_tools_iso: dict[str, Any]
    ) -> None:
        vm, param = vm_install_other_drivers
        if param.get("vendor_device"):
            pytest.skip("Skipping XenClean with vendor device present")

        set_vm_dns(vm)
        logging.info("XenClean with other tools")
        run_xenclean(vm, guest_tools_iso)
        logging.info("Check tools uninstalled")
        assert vm.are_windows_tools_uninstalled()
        check_vm_dns(vm)

    def test_xenclean_onboard_with_other_tools(
        self, vm_install_other_drivers: tuple[VM, dict[str, Any]], onboarding_guest_tools_iso: dict[str, Any]
    ) -> None:
        vm, param = vm_install_other_drivers
        onboarding_phase = param.get("onboarding_phase")
        if not param.get("onboarding_phase"):
            pytest.skip("Skipping XenClean on other tools with no defined onboarding phase")

        logging.info("XenClean onboard with other tools")
        assert run_xenclean(vm, onboarding_guest_tools_iso, onboard=True) == onboarding_phase
