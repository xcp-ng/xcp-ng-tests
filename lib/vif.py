from lib.common import _param_add, _param_clear, _param_get, _param_remove, _param_set

class VIF:
    xe_prefix = "vif"

    def __init__(self, uuid, vm):
        self.uuid = uuid
        self.vm = vm

    def plug(self):
        self.vm.host.xe("vif-plug", {'uuid': self.uuid})

    def unplug(self):
        self.vm.host.xe("vif-unplug", {'uuid': self.uuid})

    def param_get(self, param_name, key=None, accept_unknown_key=False):
        return _param_get(self.vm.host, VIF.xe_prefix, self.uuid, param_name, key, accept_unknown_key)

    def param_set(self, param_name, value, key=None):
        _param_set(self.vm.host, VIF.xe_prefix, self.uuid, param_name, value, key)

    def param_add(self, param_name, value, key=None):
        _param_add(self.vm.host, VIF.xe_prefix, self.uuid, param_name, value, key)

    def param_clear(self, param_name):
        _param_clear(self.vm.host, VIF.xe_prefix, self.uuid, param_name)

    def param_remove(self, param_name, key, accept_unknown_key=False):
        _param_remove(self.vm.host, VIF.xe_prefix, self.uuid, param_name, key, accept_unknown_key)

    def device_id(self):
        """ Build the identifier that will allow to grep for the VIF's interrupts. """
        dom_id = self.vm.param_get('dom-id')
        device = self.param_get('device')
        return f"vif{dom_id}.{device}"

    def move(self, network_uuid):
        self.vm.host.xe('vif-move', {'uuid': self.uuid, 'network-uuid': network_uuid})
