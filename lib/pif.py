from __future__ import annotations

from lib.common import XeObject, strtobool

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.host import Host

class PIF(XeObject):
    xe_prefix = "pif"

    def __init__(self, uuid: str, host: Host):
        super().__init__(host, uuid)
        self.host = host

    def is_managed(self) -> bool:
        return strtobool(self.param_get("managed"))

    def is_physical(self) -> bool:
        return strtobool(self.param_get("physical"))

    def is_currently_attached(self) -> bool:
        return strtobool(self.param_get("currently-attached"))

    def is_management(self) -> bool:
        return strtobool(self.param_get("management"))

    def device(self) -> str:
        device = self.param_get("device")
        assert device is not None
        return device

    def network_uuid(self) -> str:
        uuid = self.param_get("network-uuid")
        assert uuid is not None, "unexpected PIF without network-uuid"
        return uuid

    def ip_configuration_mode(self) -> str:
        mode = self.param_get("IP-configuration-mode")
        assert mode
        return mode

    def vlan(self) -> int | None:
        vlan_str = self.param_get('VLAN')
        assert vlan_str
        vlan = int(vlan_str)
        if vlan == -1:
            return None
        else:
            return vlan

    def reconfigure_ip(self, mode: str) -> None:
        self.host.xe("pif-reconfigure-ip", {
            "uuid": self.uuid,
            "mode": mode,
        })

    def reconfigure_ipv6(self, mode: str) -> None:
        self.host.xe("pif-reconfigure-ipv6", {
            "uuid": self.uuid,
            "mode": mode,
        })
