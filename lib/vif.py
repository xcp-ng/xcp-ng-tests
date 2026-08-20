from __future__ import annotations

import logging
import time

from lib.common import _param_add, _param_clear, _param_get, _param_remove, _param_set
from lib.network import Network

from typing import TYPE_CHECKING, Literal, overload

if TYPE_CHECKING:
    from lib.vm import VM


class VIF:
    xe_prefix = "vif"
    uuid: str
    vm: VM

    def __init__(self, uuid: str, vm: VM):
        self.uuid = uuid
        self.vm = vm

    def param_get(self, param_name: str, key: str | None = None, accept_unknown_key: bool = False) -> str | None:
        return _param_get(self.vm.host, VIF.xe_prefix, self.uuid,
                          param_name, key, accept_unknown_key)

    def param_set(self, param_name: str, value: str, key: str | None = None) -> None:
        _param_set(self.vm.host, VIF.xe_prefix, self.uuid,
                   param_name, value, key)

    def param_add(self, param_name: str, value: str, key: str | None = None) -> None:
        _param_add(self.vm.host, VIF.xe_prefix, self.uuid,
                   param_name, value, key)

    def param_clear(self, param_name: str) -> None:
        _param_clear(self.vm.host, VIF.xe_prefix, self.uuid,
                     param_name)

    def param_remove(self, param_name: str, key: str, accept_unknown_key: bool = False) -> None:
        _param_remove(self.vm.host, VIF.xe_prefix, self.uuid,
                      param_name, key, accept_unknown_key)

    def device_id(self) -> str:
        """ Build the identifier that will allow to grep for the VIF's interrupts. """
        dom_id = self.vm.param_get('dom-id')
        device = self.param_get('device')
        return f"vif{dom_id}.{device}"

    def move(self, network_uuid: str) -> None:
        self.vm.host.xe('vif-move', {'uuid': self.uuid, 'network-uuid': network_uuid})

    def destroy(self) -> None:
        logging.info("Destroying VIF %s on VM %s", self.param_get('device'), self.vm.uuid)
        self.vm.host.xe('vif-destroy', {'uuid': self.uuid})

    def mac_address(self) -> str:
        mac_address = self.param_get('MAC')
        assert mac_address is not None, "VIF must have a MAC address"
        return mac_address

    def plug(self) -> None:
        logging.info("Plugging VIF %s on VM %s", self.param_get('device'), self.vm.uuid)
        self.vm.host.xe('vif-plug', {'uuid': self.uuid})

    def unplug(self, force: bool = False) -> None:
        logging.info("Unplugging VIF %s on VM %s", self.param_get('device'), self.vm.uuid)
        self.vm.host.xe('vif-unplug', {'uuid': self.uuid, 'force': force})

    def _configure(
        self,
        address_family: str,
        mode: str,
        address: str | None = None,
        gateway: str | None = None,
    ) -> None:
        args: dict[str, str | bool | dict[str, str]] = {"uuid": self.uuid, "mode": mode}
        if address is not None:
            args["address"] = address
        if gateway is not None:
            args["gateway"] = gateway
        self.vm.host.xe(f"vif-configure-{address_family}", args)
        # HACK: xe returns after publishing the request, before the guest has acknowledged it. Give the guest time to
        # consume it so a following request cannot overwrite the pending one.
        time.sleep(5)

    @overload
    def configure_ipv4(
        self,
        mode: Literal["static"],
        address: str,
        gateway: str | None = None,
    ) -> None:  #
        ...

    @overload
    def configure_ipv4(
        self,
        mode: Literal["dhcp"] | Literal["none"],
        address: None = None,
        gateway: None = None,
    ) -> None:  #
        ...

    def configure_ipv4(
        self,
        mode: str,
        address: str | None = None,
        gateway: str | None = None,
    ) -> None:
        self._configure("ipv4", mode, address, gateway)

    @overload
    def configure_ipv6(
        self,
        mode: Literal["static"],
        address: str,
        gateway: str | None = None,
    ) -> None:  #
        ...

    @overload
    def configure_ipv6(
        self,
        mode: Literal["autoconf"] | Literal["none"],
        address: None = None,
        gateway: None = None,
    ) -> None:  #
        ...

    def configure_ipv6(
        self,
        mode: str,
        address: str | None = None,
        gateway: str | None = None,
    ) -> None:
        self._configure("ipv6", mode, address, gateway)

    def network(self) -> Network:
        network_uuid = self.param_get('network-uuid')
        assert network_uuid is not None, "VIF must have a network-uuid"
        return Network(self.vm.host, network_uuid)
