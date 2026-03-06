import enum
import logging
import re
import time

from data import ISO_DOWNLOAD_URL, TEST_DNS_SERVER
from lib.commands import SSHCommandFailed
from lib.common import strtobool, wait_for
from lib.host import Host
from lib.sr import SR
from lib.vif import VIF
from lib.vm import VM

from typing import Any, Dict, List, Union

# HACK: I originally thought that using Stop-Computer -Force would cause the SSH session to sometimes fail.
# I could never confirm this in the end, but use a slightly delayed shutdown just to be safe anyway.
WINDOWS_SHUTDOWN_COMMAND = "shutdown.exe -s -f -t 5"


class PowerAction(enum.Enum):
    Nothing = "nothing"
    Shutdown = "shutdown"
    Reboot = "reboot"


def iso_create(host: Host, sr: SR, param: Dict[str, Any]):
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


def enable_testsign(vm: VM, rootcert: Union[str, None]):
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


def vif_get_mac_without_separator(vif: VIF):
    mac = vif.param_get("MAC")
    assert mac is not None
    return mac.replace(":", "")


def vif_has_rss(vif: VIF):
    # Even if the Xenvif hash setting request fails, Windows can still report the NIC as having RSS enabled as long as
    # the relevant OIDs are supported (Get-NetAdapterRss reports Enabled as True and Profile as Default).
    # We need to explicitly check MaxProcessors to see if the hash setting request has really succeeded.
    mac = vif_get_mac_without_separator(vif)
    return strtobool(
        vif.vm.execute_powershell_script(
            rf"""(Get-NetAdapter |
Where-Object {{$_.PnPDeviceID -notlike 'root\kdnic\*' -and $_.PermanentAddress -eq '{mac}'}} |
Get-NetAdapterRss).MaxProcessors -gt 0"""
        )
    )


def vif_get_dns(vif: VIF):
    mac = vif_get_mac_without_separator(vif)
    return vif.vm.execute_powershell_script(
        rf"""Import-Module DnsClient; Get-NetAdapter |
Where-Object {{$_.PnPDeviceID -notlike 'root\kdnic\*' -and $_.PermanentAddress -eq '{mac}'}} |
Get-DnsClientServerAddress -AddressFamily IPv4 |
Select-Object -ExpandProperty ServerAddresses"""
    ).splitlines()


def vif_set_dns(vif: VIF, nameservers: List[str]):
    mac = vif_get_mac_without_separator(vif)
    vif.vm.execute_powershell_script(
        rf"""Import-Module DnsClient; Get-NetAdapter |
Where-Object {{$_.PnPDeviceID -notlike 'root\kdnic\*' -and $_.PermanentAddress -eq '{mac}'}} |
Get-DnsClientServerAddress -AddressFamily IPv4 |
Set-DnsClientServerAddress -ServerAddresses {",".join(nameservers)}"""
    )


def wait_for_vm_xenvif_offboard(vm: VM):
    # Xenvif offboard will reset the NIC, so need to wait for it to disappear first
    wait_for(
        lambda: strtobool(
            vm.execute_powershell_script(
                r'$null -eq (Get-ScheduledTask "Copy-XenVifSettings" -ErrorAction SilentlyContinue)', simple_output=True
            )
        ),
        timeout_secs=300,
        retry_delay_secs=30,
    )


def set_vm_dns(vm: VM):
    logging.info(f"Set VM DNS to {TEST_DNS_SERVER}")
    vif = vm.vifs()[0]
    assert TEST_DNS_SERVER not in vif_get_dns(vif)
    vif_set_dns(vif, [TEST_DNS_SERVER])


def check_vm_dns(vm: VM):
    # The restore task takes time to fire so wait for it
    vif = vm.vifs()[0]
    wait_for(
        lambda: TEST_DNS_SERVER in vif_get_dns(vif),
        f"Check VM DNS contains {TEST_DNS_SERVER}",
        timeout_secs=300,
        retry_delay_secs=30,
    )


def check_vm_distro(vm: VM):
    # anything goes, as long as it's not empty
    wait_for(
        lambda: vm.xenstore_read("data/os_distro", accept_unknown_key=True) is not None,
        "Wait for distro reporting",
        30,
    )


def check_vm_clipboard(vm: VM):
    # Key must not exist prior to our write. If it does, likely nothing is watching
    assert vm.xenstore_read("data/set_clipboard", accept_unknown_key=True) is None
    vm.xenstore_write("data/set_clipboard", "foobar")
    # Now the guest agent should have erased it
    wait_for(
        lambda: vm.xenstore_read("data/set_clipboard", accept_unknown_key=True) is None,
        "Wait for guest agent to receive data/set_clipboard",
        30,
    )
    # Must terminate the clipboard string with an empty fragment
    vm.xenstore_write("data/set_clipboard", '""')
    wait_for(
        lambda: vm.xenstore_read("data/set_clipboard", accept_unknown_key=True) is None,
        "Wait for guest agent to receive data/set_clipboard",
        30,
    )
