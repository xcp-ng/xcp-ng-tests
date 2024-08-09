import logging

from lib.common import _param_add, _param_clear, _param_get, _param_remove, _param_set

class VDI:
    xe_prefix = "vdi"

    def __init__(self, sr, uuid):
        self.uuid = uuid
        # TODO: use a different approach when migration is possible
        self.sr = sr

    def destroy(self):
        logging.info("Destroy %s", self)
        self.sr.pool.master.xe('vdi-destroy', {'uuid': self.uuid})

    def __str__(self):
        return f"VDI {self.uuid} on SR {self.sr.uuid}"

    def param_get(self, param_name, key=None, accept_unknown_key=False):
        return _param_get(self.sr.pool.master, self.xe_prefix, self.uuid,
                          param_name, key, accept_unknown_key)

    def param_set(self, param_name, value, key=None):
        _param_set(self.sr.pool.master, self.xe_prefix, self.uuid,
                   param_name, value, key)

    def param_add(self, param_name, value, key=None):
        _param_add(self.sr.pool.master, self.xe_prefix, self.uuid,
                   param_name, value, key)

    def param_clear(self, param_name):
        _param_clear(self.sr.pool.master, self.xe_prefix, self.uuid,
                     param_name)

    def param_remove(self, param_name, key, accept_unknown_key=False):
        _param_remove(self.sr.pool.master, self.xe_prefix, self.uuid,
                      param_name, key, accept_unknown_key)
