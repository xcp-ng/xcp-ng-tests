import enum
import logging
import re
from typing import Any
from data import ISO_DOWNLOAD_URL
from lib.common import wait_for
from lib.host import Host
from lib.vm import VM


class PowerAction(enum.Enum):
    Nothing = "nothing"
    Shutdown = "shutdown"
    Reboot = "reboot"


def iso_create(host: Host, param: dict[str, Any]):
    if param["download"]:
        vdi = host.import_iso(ISO_DOWNLOAD_URL + param["name"], host.iso_sr_uuid())
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
    report = vm.host.ssh(command)
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
    vm.execute_powershell_script("bcdedit /set testsigning on; if ($LASTEXITCODE -ne 0) {throw}; Stop-Computer -Force")
    wait_for(vm.is_halted, "Wait for VM halted")
    vm.start()
    wait_for_vm_running_and_ssh_up_without_tools(vm)
