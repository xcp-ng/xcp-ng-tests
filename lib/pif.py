from __future__ import annotations

from lib.common import _param_add, _param_clear, _param_get, _param_remove, _param_set, strtobool

from typing import TYPE_CHECKING, Literal, overload

if TYPE_CHECKING:
    from lib.host import Host

class PIF:
    xe_prefix = "pif"

    def __init__(self, uuid: str, host: Host):
        self.uuid = uuid
        self.host = host

    @overload
    def param_get(self, param_name: str, key: str | None = ...,
                  accept_unknown_key: Literal[False] = ...) -> str:
        ...

    @overload
    def param_get(self, param_name: str, key: str | None = ...,
                  accept_unknown_key: Literal[True] = ...) -> str | None:
        ...

    def param_get(self, param_name: str, key: str | None = None, accept_unknown_key: bool = False) -> str | None:
        return _param_get(self.host, self.xe_prefix, self.uuid,
                          param_name, key, accept_unknown_key)

    def param_set(self, param_name: str, value: str, key: str | None = None) -> None:
        _param_set(self.host, self.xe_prefix, self.uuid,
                   param_name, value, key)

    def param_remove(self, param_name: str, key: str, accept_unknown_key: bool = False) -> None:
        _param_remove(self.host, self.xe_prefix, self.uuid,
                      param_name, key, accept_unknown_key)

    def param_add(self, param_name: str, value: str, key: str | None = None) -> None:
        _param_add(self.host, self.xe_prefix, self.uuid,
                   param_name, value, key)

    def param_clear(self, param_name: str) -> None:
        _param_clear(self.host, self.xe_prefix, self.uuid,
                     param_name)

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
        assert uuid is not None
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
