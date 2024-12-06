import logging
from pathlib import PureWindowsPath
from typing import Any

from lib.common import wait_for
from lib.vm import VM
from . import PowerAction, enable_testsign, wait_for_vm_running_and_ssh_up_without_tools


ERROR_SUCCESS = 0
ERROR_INSTALL_FAILURE = 1603
ERROR_SUCCESS_REBOOT_INITIATED = 1641
ERROR_SUCCESS_REBOOT_REQUIRED = 3010

GUEST_TOOLS_COPY_PATH = "C:\\package.msi"


def install_guest_tools(vm: VM, guest_tools_iso: dict[str, Any], action: PowerAction, check: bool = True):
    vm.insert_cd(guest_tools_iso["name"])
    wait_for(lambda: vm.file_exists("D:/", regular_file=False))

    if guest_tools_iso.get("testsign_cert"):
        logging.info("Enable testsigning")
        rootcert = PureWindowsPath("D:\\") / guest_tools_iso["testsign_cert"]
        enable_testsign(vm, rootcert)

    logging.info("Copy Windows PV drivers to VM")
    package_path = PureWindowsPath("D:\\") / guest_tools_iso["package"]
    vm.execute_powershell_script(f"Copy-Item -Force '{package_path}' '{GUEST_TOOLS_COPY_PATH}'")

    vm.eject_cd()

    logging.info("Install Windows PV drivers")
    msiexec_args = f"/i {GUEST_TOOLS_COPY_PATH} /log C:\\tools_install.log /passive /norestart"

    if action == PowerAction.Nothing:
        exitcode = vm.run_powershell_command("msiexec.exe", msiexec_args)
    else:
        if check:
            raise Exception(f"Cannot check exit code with {action} action")
        # when powershell runs msiexec it doesn't wait for it to end unlike ssh
        # it only waits for stdin closing so we need Start-Process -Wait here
        install_cmd = f"Start-Process -Wait msiexec.exe -ArgumentList '{msiexec_args}'"
        if action != PowerAction.Nothing:
            install_cmd += ";Stop-Computer -Force"
        vm.start_background_powershell(install_cmd)
        if action != PowerAction.Nothing:
            wait_for(vm.is_halted, "Wait for VM halted")
        if action == PowerAction.Reboot:
            vm.start()
            wait_for_vm_running_and_ssh_up_without_tools(vm)
        exitcode = None

    if check:
        assert exitcode in [ERROR_SUCCESS, ERROR_SUCCESS_REBOOT_INITIATED, ERROR_SUCCESS_REBOOT_REQUIRED]
    return exitcode


def uninstall_guest_tools(vm: VM, action: PowerAction):
    msiexec_args = f"/x {GUEST_TOOLS_COPY_PATH} /log C:\\tools_uninstall.log /passive /norestart"
    uninstall_cmd = f"Start-Process -Wait msiexec.exe -ArgumentList '{msiexec_args}'"
    if action != PowerAction.Nothing:
        uninstall_cmd += ";Stop-Computer -Force"
    vm.start_background_powershell(uninstall_cmd)
    if action != PowerAction.Nothing:
        wait_for(vm.is_halted, "Wait for VM halted")
    if action == PowerAction.Reboot:
        vm.start()
        wait_for_vm_running_and_ssh_up_without_tools(vm)
