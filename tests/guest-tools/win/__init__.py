import enum
import logging
import re
import time
from typing import Any

from data import ISO_DOWNLOAD_URL
from lib.commands import SSHCommandFailed
from lib.common import wait_for
from lib.host import Host
from lib.sr import SR
from lib.vm import VM


# HACK: I originally thought that using Stop-Computer -Force would cause the SSH session to sometimes fail.
# I could never confirm this in the end, but use a slightly delayed shutdown just to be safe anyway.
WINDOWS_SHUTDOWN_COMMAND = "shutdown.exe -s -f -t 5"


class PowerAction(enum.Enum):
    Nothing = "nothing"
    Shutdown = "shutdown"
    Reboot = "reboot"


def iso_create(host: Host, sr: SR, param: dict[str, Any]):
    if param["download"]:
        vdi = host.import_iso(ISO_DOWNLOAD_URL + param["name"], sr)
        new_param = param.copy()
        new_param["name"] = vdi.name()
        yield new_param
        vdi.destroy()
    else:
        yield param


def try_get_and_store_vm_ip_serial(vm: VM, timeout: int):
    domid = vm.param_get("dom-id")
    logging.debug(f"Domain ID {domid}")
    command = f"xl console -t serial {domid} | grep '~xcp-ng-tests~.*~end~' | head -n 1"
    if timeout > 0:
        command = f"timeout {timeout} " + command
    res_host = vm.get_residence_host()
    report = res_host.ssh(command)
    logging.debug(f"Got report: {report}")
    match = re.match("~xcp-ng-tests~(.*)=(.*)~end~", report)
    if not match:
        return False
    vm.ip = match[2]
    return True


def wait_for_vm_running_and_ssh_up_without_tools(vm: VM):
    wait_for(vm.is_running, "Wait for VM running")
    wait_for(vm.is_ssh_up, "Wait for SSH up")


def enable_testsign(vm: VM, rootcert: str | None):
    if rootcert is not None:
        vm.execute_powershell_script(
            f"""certutil -addstore -f Root '{rootcert}';
if ($LASTEXITCODE -ne 0) {{throw}}
certutil -addstore -f TrustedPublisher '{rootcert}';
if ($LASTEXITCODE -ne 0) {{throw}}"""
        )
    vm.execute_powershell_script(
        "bcdedit /set testsigning on; if ($LASTEXITCODE -ne 0) {throw};" + WINDOWS_SHUTDOWN_COMMAND
    )
    wait_for(vm.is_halted, "Wait for VM halted")
    vm.start()
    wait_for_vm_running_and_ssh_up_without_tools(vm)


def insert_cd_safe(vm: VM, vdi_name: str, cd_path="D:/", retries=2):
    """
    Insert a CD with retry.

    HACK: Windows VM may not detect the CD drive in some cases.
    If this happens, reboot the VM in hopes that it will be detected.
    """
    for _attempt in range(retries):
        # Eject the existing CD just in case.
        try:
            vm.eject_cd()
            # Leave some time for the guest to realize its CD got ejected.
            time.sleep(5)
        except SSHCommandFailed:
            pass

        vm.insert_cd(vdi_name)
        if not vm.is_running():
            vm.start()
        # wait_for(vm.file_exists) doesn't handle SSHCommandFailed;
        # we need to check for it via wait_for(vm.is_ssh_up).
        wait_for_vm_running_and_ssh_up_without_tools(vm)

        try:
            wait_for(lambda: vm.file_exists(cd_path, regular_file=False), timeout_secs=60)
            return
        except TimeoutError:
            logging.warning(f"Waiting for CD at {cd_path} failed, retrying by rebooting VM")
            # There might be no VM tools so use SSH instead.
            vm.ssh(WINDOWS_SHUTDOWN_COMMAND)
            wait_for(vm.is_halted, "Wait for VM halted")

    raise TimeoutError(f"Waiting for CD at {cd_path} failed")
