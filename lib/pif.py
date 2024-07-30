import lib.commands as commands

from lib.common import _param_add, _param_clear, _param_get, _param_remove, _param_set

class PIF:
    xe_prefix = "pif"

    def __init__(self, uuid, host):
        self.uuid = uuid
        self.host = host

    def param_get(self, param_name, key=None, accept_unknown_key=False):
        return _param_get(self.host, PIF.xe_prefix, self.uuid, param_name, key, accept_unknown_key)

    def param_set(self, param_name, value, key=None):
        _param_set(self.host, PIF.xe_prefix, self.uuid, param_name, value, key)

    def param_add(self, param_name, value, key=None):
        _param_add(self.host, PIF.xe_prefix, self.uuid, param_name, value, key)

    def param_clear(self, param_name):
        _param_clear(self.host, PIF.xe_prefix, self.uuid, param_name)

    def param_remove(self, param_name, key, accept_unknown_key=False):
        _param_remove(self.host, PIF.xe_prefix, self.uuid, param_name, key, accept_unknown_key)
