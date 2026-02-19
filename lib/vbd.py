from __future__ import annotations

import logging

from lib.common import _param_add, _param_clear, _param_get, _param_remove, _param_set

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.vm import VM

class VBD:
    xe_prefix = "vbd"
    uuid: str
    vm: VM
    device: str

    def __init__(self, uuid: str, vm: VM, device: str):
        self.uuid = uuid
        self.vm = vm
        self.device = device

    def plug(self) -> None:
        self.vm.host.xe("vbd-plug", {'uuid': self.uuid})

    def unplug(self) -> None:
        self.vm.host.xe("vbd-unplug", {'uuid': self.uuid})

    def param_get(self, param_name: str, key: str | None = None, accept_unknown_key: bool = False) -> str | None:
        return _param_get(self.vm.host, self.xe_prefix, self.uuid,
                          param_name, key, accept_unknown_key)

    def param_set(self, param_name: str, value: str, key: str | None = None) -> None:
        _param_set(self.vm.host, self.xe_prefix, self.uuid,
                   param_name, value, key)

    def param_remove(self, param_name: str, key: str, accept_unknown_key: bool = False) -> None:
        _param_remove(self.vm.host, self.xe_prefix, self.uuid,
                      param_name, key, accept_unknown_key)

    def param_add(self, param_name: str, value: str, key: str | None = None) -> None:
        _param_add(self.vm.host, self.xe_prefix, self.uuid,
                   param_name, value, key)

    def param_clear(self, param_name: str) -> None:
        _param_clear(self.vm.host, self.xe_prefix, self.uuid,
                     param_name)

    def destroy(self) -> None:
        logging.info("Destroy %s", self)
        self.vm.host.pool.master.xe('vbd-destroy', {'uuid': self.uuid})

    def __str__(self) -> str:
        return f"VBD {self.uuid} for {self.device} of VM {self.vm.uuid}"
