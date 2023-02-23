import lib.commands as commands

from lib.common import _param_get

class VIF:
    xe_prefix = "vif"

    def __init__(self, uuid, vm):
        self.uuid = uuid
        self.vm = vm

    def param_get(self, param_name, key=None, accept_unknown_key=False):
        return _param_get(self.vm.host, VIF.xe_prefix, self.uuid, param_name, key, accept_unknown_key)

    def device_id(self):
        """ Build the identifier that will allow to grep for the VIF's interrupts. """
        dom_id = self.vm.param_get('dom-id')
        device = self.param_get('device')
        return f"vif{dom_id}.{device}"

    def move(self, network_uuid):
        self.vm.host.xe('vif-move', {'uuid': self.uuid, 'network-uuid': network_uuid})
