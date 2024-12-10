import logging
from pathlib import PureWindowsPath
from typing import Any

from lib.common import wait_for
from lib.vm import VM
from . import enable_testsign, wait_for_vm_running_and_ssh_up_without_tools


def install_other_drivers(vm: VM, other_tools_iso_name: str, param: dict[str, Any]):
    if param.get("vendor_device"):
        assert not vm.param_get("has-vendor-device")
        vm.param_set("has-vendor-device", True)

    vm.start()
    wait_for_vm_running_and_ssh_up_without_tools(vm)

    vm.insert_cd(other_tools_iso_name)
    wait_for(lambda: vm.file_exists("D:/", regular_file=False))

    if param.get("testsign_cert"):
        logging.info("Enable testsigning")
        rootcert = PureWindowsPath("D:\\") / param["path"] / param["testsign_cert"]
        enable_testsign(vm, rootcert)

    package_path = PureWindowsPath("D:\\") / param["path"] / param["package"]
    install_cmd = "D:\\install-drivers.ps1 -Shutdown "
    if param["is_msi"]:
        logging.info(f"Install MSI drivers: {package_path}")
        install_cmd += f"-MsiPath '{package_path}' "
    else:
        logging.info(f"Install drivers: {package_path}")
        install_cmd += f"-DriverPath '{package_path}' "
    install_cmd += ">C:\\othertools.log"
    vm.start_background_powershell(install_cmd)
    wait_for(vm.is_halted, "Shutdown VM")

    vm.eject_cd()
    vm.start()
    wait_for_vm_running_and_ssh_up_without_tools(vm)
