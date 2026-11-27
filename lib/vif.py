from __future__ import annotations

import logging
import time

from lib.common import XeObject, XeParams
from lib.network import Network

from typing import TYPE_CHECKING, Literal, overload

if TYPE_CHECKING:
    from lib.vm import VM


class VIF(XeObject):
    xe_prefix = "vif"
    uuid: str
    vm: VM

    def __init__(self, uuid: str, vm: VM):
        super().__init__(vm.host, uuid)
        self.vm = vm

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
        args: XeParams = {"uuid": self.uuid, "mode": mode}
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
        gateway: str | None = ...,
    ) -> None:  #
        ...

    @overload
    def configure_ipv4(
        self,
        mode: Literal["dhcp"] | Literal["none"],
        address: None = ...,
        gateway: None = ...,
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
        gateway: str | None = ...,
    ) -> None:  #
        ...

    @overload
    def configure_ipv6(
        self,
        mode: Literal["autoconf"] | Literal["none"],
        address: None = ...,
        gateway: None = ...,
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
