import logging
from pathlib import PureWindowsPath
from typing import Any

from lib.common import wait_for
from lib.vm import VM
from . import WINDOWS_SHUTDOWN_COMMAND, enable_testsign, insert_cd_safe, wait_for_vm_running_and_ssh_up_without_tools


def install_other_drivers(vm: VM, other_tools_iso_name: str, param: dict[str, Any]):
    if param.get("vendor_device"):
        assert not vm.param_get("has-vendor-device")
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

        package_path = PureWindowsPath("D:\\") / param["path"] / param["package"]
        install_cmd = "D:\\install-drivers.ps1 "
        if driver_type == "msi":
            logging.info(f"Install MSI drivers: {package_path}")
            install_cmd += f"-MsiPath '{package_path}' "
        elif driver_type == "inf":
            logging.info(f"Install drivers: {package_path}")
            install_cmd += f"-DriverPath '{package_path}' "
        else:
            raise RuntimeError(f"Invalid driver package type {driver_type}")
        install_cmd += ">C:\\othertools.log;"
        install_cmd += WINDOWS_SHUTDOWN_COMMAND
        vm.start_background_powershell(install_cmd)
        # Leave some extra time for install-drivers.ps1 to work as 2 mins may not be enough for it.
        wait_for(vm.is_halted, "Shutdown VM", timeout_secs=300)

        vm.eject_cd()
        vm.start()
        wait_for_vm_running_and_ssh_up_without_tools(vm)
