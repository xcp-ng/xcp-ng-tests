from __future__ import annotations

import logging

from lib.common import XeObject

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.vm import VM

class VBD(XeObject):
    xe_prefix = "vbd"
    uuid: str
    vm: VM
    device: str

    def __init__(self, uuid: str, vm: VM, device: str):
        super().__init__(vm.host, uuid)
        self.uuid = uuid
        self.vm = vm
        self.device = device

    def plug(self) -> None:
        self.vm.host.xe("vbd-plug", {'uuid': self.uuid})

    def unplug(self) -> None:
        self.vm.host.xe("vbd-unplug", {'uuid': self.uuid})

    def destroy(self) -> None:
        logging.info("Destroy %s", self)
        self.vm.host.pool.master.xe('vbd-destroy', {'uuid': self.uuid})

    def __str__(self) -> str:
        return f"VBD {self.uuid} for {self.device} of VM {self.vm.uuid}"
