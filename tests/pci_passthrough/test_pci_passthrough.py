import pytest

import logging

from lib.host import Host

# Requirements:
# - an XCP-ng host (--hosts) >= 8.3 with a PGPU to hide from dom0

XEN_CMDLINE = '/opt/xensource/libexec/xen-cmdline'

@pytest.mark.reboot # reboots the host
@pytest.mark.usefixtures("host_at_least_8_3")
class TestPCIPassthrough:
    def test_pci_dom0_access(self, host: Host, enabled_pci_uuid: str) -> None:
        host.xe("pci-disable-dom0-access", {"uuid": enabled_pci_uuid})
        assert host.xe("pci-get-dom0-access-status", {"uuid": enabled_pci_uuid}) == "disable_on_reboot"
        host.reboot(verify=True)
        assert host.xe("pci-get-dom0-access-status", {"uuid": enabled_pci_uuid}) == "disabled"

        host.xe("pci-enable-dom0-access", {"uuid": enabled_pci_uuid})
        assert host.xe("pci-get-dom0-access-status", {"uuid": enabled_pci_uuid}) == "enable_on_reboot"
        host.reboot(verify=True)
        assert host.xe("pci-get-dom0-access-status", {"uuid": enabled_pci_uuid}) == "enabled"

    def test_access_status_manual_modification(self, host: Host, enabled_pci_uuid: str) -> None:
        device_id = host.xe("pci-param-get", {"uuid": enabled_pci_uuid, "param-name": "pci-id"})
        hidden_devices = host.ssh(f'{XEN_CMDLINE} --get-dom0 "xen-pciback.hide"')
        if hidden_devices == "":
            hidden_devices = "xen-pciback.hide="
        devices = hidden_devices + f"({device_id})"

        host.ssh(f'{XEN_CMDLINE} --set-dom0 "{devices}"')
        assert host.xe("pci-get-dom0-access-status", {"uuid": enabled_pci_uuid}) == "disable_on_reboot"
        host.reboot(verify=True)
        assert host.xe("pci-get-dom0-access-status", {"uuid": enabled_pci_uuid}) == "disabled"

        host.ssh(f'{XEN_CMDLINE} --set-dom0 "{hidden_devices}"')
        assert host.xe("pci-get-dom0-access-status", {"uuid": enabled_pci_uuid}) == "enable_on_reboot"
        host.reboot(verify=True)
        assert host.xe("pci-get-dom0-access-status", {"uuid": enabled_pci_uuid}) == "enabled"

@pytest.mark.reboot # reboots the host
@pytest.mark.usefixtures("host_at_least_8_3")
class TestPGPUPCIDom0AccessInheritance:
    def test_pci_sync_dom0_access(self, host: Host, enabled_pgpu_uuid: str) -> None:
        pci_uuid = host.xe("pgpu-param-get", {"uuid": enabled_pgpu_uuid, "param-name": "pci-uuid"})
        host.xe("pci-disable-dom0-access", {"uuid": pci_uuid})
        assert host.xe(
            "pgpu-param-get", {"uuid": enabled_pgpu_uuid, "param-name": "dom0-access"}
        ) == "disable_on_reboot"

        host.reboot(verify=True)
        assert host.xe("pgpu-param-get", {"uuid": enabled_pgpu_uuid, "param-name": "dom0-access"}) == "disabled"

        pci_uuid = host.xe("pgpu-param-get", {"uuid": enabled_pgpu_uuid, "param-name": "pci-uuid"})
        host.xe("pci-enable-dom0-access", {"uuid": pci_uuid})
        assert host.xe("pgpu-param-get", {"uuid": enabled_pgpu_uuid, "param-name": "dom0-access"}) == "enable_on_reboot"

        host.reboot(verify=True)
        assert host.xe("pgpu-param-get", {"uuid": enabled_pgpu_uuid, "param-name": "dom0-access"}) == "enabled"

    def test_pgpu_sync_dom0_access(self, host: Host, enabled_pgpu_uuid: str) -> None:
        pci_uuid = host.xe("pgpu-param-get", {"uuid": enabled_pgpu_uuid, "param-name": "pci-uuid"})
        host.xe("pgpu-disable-dom0-access", {"uuid": enabled_pgpu_uuid})
        assert host.xe(
            "pgpu-param-get", {"uuid": enabled_pgpu_uuid, "param-name": "dom0-access"}
        ) == "disable_on_reboot"
        assert host.xe("pci-get-dom0-access-status", {"uuid": pci_uuid}) == "disable_on_reboot"

        host.reboot(verify=True)
        assert host.xe("pgpu-param-get", {"uuid": enabled_pgpu_uuid, "param-name": "dom0-access"}) == "disabled"
        assert host.xe("pci-get-dom0-access-status", {"uuid": pci_uuid}) == "disabled"

        pci_uuid = host.xe("pgpu-param-get", {"uuid": enabled_pgpu_uuid, "param-name": "pci-uuid"})
        host.xe("pgpu-enable-dom0-access", {"uuid": enabled_pgpu_uuid})
        assert host.xe("pgpu-param-get", {"uuid": enabled_pgpu_uuid, "param-name": "dom0-access"}) == "enable_on_reboot"
        assert host.xe("pci-get-dom0-access-status", {"uuid": pci_uuid}) == "enable_on_reboot"

        host.reboot(verify=True)
        assert host.xe("pgpu-param-get", {"uuid": enabled_pgpu_uuid, "param-name": "dom0-access"}) == "enabled"
        assert host.xe("pci-get-dom0-access-status", {"uuid": pci_uuid}) == "enabled"
