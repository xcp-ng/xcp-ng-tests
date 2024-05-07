import logging

from lib.common import _param_get, _param_remove, _param_set

class VBD:
    xe_prefix = "vbd"

    def __init__(self, uuid, vm, device):
        self.uuid = uuid
        self.vm = vm
        self.device = device

    def param_get(self, param_name, key=None, accept_unknown_key=False):
        return _param_get(self.vm.host, VBD.xe_prefix, self.uuid, param_name, key, accept_unknown_key)

    def param_set(self, param_name, value, key=None):
        _param_set(self.vm.host, VBD.xe_prefix, self.uuid, param_name, value, key)

    def param_remove(self, param_name, key, accept_unknown_key=False):
        _param_remove(self.vm.host, VBD.xe_prefix, self.uuid, param_name, key, accept_unknown_key)

    def destroy(self):
        logging.info("Destroy %s", self)
        self.vm.host.pool.master.xe('vbd-destroy', {'uuid': self.uuid})

    def __str__(self):
        return f"VBD {self.uuid} for {self.device} of VM {self.vm.uuid}"
