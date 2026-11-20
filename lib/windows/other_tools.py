import logging
from pathlib import Path, PureWindowsPath

from lib.commands import SSHCommandFailed
from lib.common import strtobool, wait_for
from lib.vm import VM

from . import (
    WINDOWS_SHUTDOWN_COMMAND,
    enable_testsign,
    insert_cd_safe,
    vm_shutdown_without_tools,
    wait_for_vm_running_and_ssh_up_without_tools,
)

from typing import Any

def get_xenserver_install_status(vm: VM) -> str | None:
    try:
        status = vm.execute_powershell_script(
            "(Get-ItemProperty -Path HKLM:\\SOFTWARE\\XenServer\\XenTools\\InstallAgent -Name InstallStatus "
            + "-ErrorAction SilentlyContinue).InstallStatus"
        ).strip()
    except SSHCommandFailed:
        # The VM can decide to reload the network drivers, which can cause the script to fail.
        return None
    # We want to keep waiting anyway if the status is Installing or Failed
    return status if status in ("Installed", "NeedsReboot") else None


def wait_for_xenserver_install_agent(vm: VM, max_reboots: int = 3) -> None:
    """
    The XenServer InstallAgent may reload NICs among other things until it marks the installation as done.
    """
    for _ in range(max_reboots + 1):
        status = wait_for(
            lambda: get_xenserver_install_status(vm),
            "Wait for XenServer InstallAgent",
            timeout_secs=300,
            retry_delay_secs=15,
        )
        if status == "Installed":
            return
        logging.info("XenServer InstallAgent needs a reboot")
        vm_shutdown_without_tools(vm)
        vm.start()
        wait_for_vm_running_and_ssh_up_without_tools(vm)
    raise TimeoutError("XenServer InstallAgent needs too many reboots")


def install_other_drivers(vm: VM, other_tools_iso_name: str, param: dict[str, Any]) -> None:
    if param.get("vendor_device"):
        assert not strtobool(vm.param_get("has-vendor-device"))
        vm.param_set("has-vendor-device", True)

    vm.start()
    wait_for_vm_running_and_ssh_up_without_tools(vm)

    driver_type = param.get("type")
    if driver_type is not None:
        insert_cd_safe(vm, other_tools_iso_name)

        if param.get("testsign_cert"):
            logging.info("Enable testsigning")
            rootcert = PureWindowsPath("D:\\") / param["path"] / param["testsign_cert"]
            enable_testsign(vm, rootcert)

            # HACK: Sometimes after rebooting the CD drive just vanishes. Check for it again and
            # reboot/reinsert CD if needed.
            if not vm.file_exists("D:/", regular_file=False):
                logging.warning("CD drive not detected, retrying")
                insert_cd_safe(vm, other_tools_iso_name)

        logging.info("Copying install-drivers.ps1")
        # sftp sees the guest's C: drive as its root, see VM.start_background_process().
        vm.sftp_put(
            str(Path(__file__).parents[2] / "scripts" / "guests" / "windows" / "install-drivers.ps1"),
            "/install-drivers.ps1",
        )

        root_path = PureWindowsPath("D:\\") / param["path"]
        package_path = root_path / param["package"]
        install_cmd = "C:\\install-drivers.ps1 "
        if driver_type == "msi":
            logging.info(f"Install MSI drivers: {package_path}")
            install_cmd += f"-MsiPath '{package_path}' "

            patches = param.get("patches", [])
            if patches:
                logging.info("Install MSP patches")
                patch_paths = [f"'{root_path / patch}'" for patch in patches]
                install_cmd += f"-Patches {','.join(patch_paths)} "

            if param.get("is_xenserver_msi"):
                install_cmd += "-WaitXenServerInstallAgent "
        elif driver_type == "inf":
            logging.info(f"Install drivers: {package_path}")
            install_cmd += f"-DriverPath '{package_path}' "
        else:
            raise RuntimeError(f"Invalid driver package type {driver_type}")
        install_cmd += ">C:\\othertools.log;"
        install_cmd += WINDOWS_SHUTDOWN_COMMAND
        vm.start_background_powershell(install_cmd)
        # Leave some extra time for install-drivers.ps1 to work as 2 mins may not be enough for it.
        # It may also wait up to 5 mins for the XenServer InstallAgent.
        wait_for(vm.is_halted, "Shutdown VM", timeout_secs=600)

        vm.eject_cd()
        vm.start()
        wait_for_vm_running_and_ssh_up_without_tools(vm)
        if param.get("is_xenserver_msi"):
            wait_for_xenserver_install_agent(vm)
        assert vm.are_windows_tools_working()
