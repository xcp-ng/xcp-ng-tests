import logging
from typing import Optional

from lib.common import _param_add, _param_clear, _param_get, _param_remove, _param_set
from typing import Literal, Optional, overload, TYPE_CHECKING
if TYPE_CHECKING:
    from lib.host import Host
    from lib.sr import SR

class VDI:
    xe_prefix = "vdi"
    sr: "SR"

    @overload
    def __init__(self, uuid: str, *, host: "Host", sr: Literal[None] = None):
        ...

    @overload
    def __init__(self, uuid, *, host: Literal[None] = None, sr: "SR"):
        ...

    def __init__(self, uuid, *, host=None, sr=None):
        self.uuid = uuid
        # TODO: use a different approach when migration is possible
        if sr is None:
            assert host
            self.sr = host.get_sr_from_vdi_uuid(self.uuid)
        else:
            self.sr = sr

    def name(self):
        return self.param_get('name-label')

    def destroy(self):
        logging.info("Destroy %s", self)
        self.sr.pool.master.xe('vdi-destroy', {'uuid': self.uuid})

    def clone(self):
        uuid = self.sr.pool.master.xe('vdi-clone', {'uuid': self.uuid})
        return VDI(uuid, sr=self.sr)

    def readonly(self):
        return self.param_get("read-only") == "true"

    def __str__(self):
        return f"VDI {self.uuid} on SR {self.sr.uuid}"

    def get_parent(self) -> Optional[str]:
        return self.param_get("sm-config", key="vhd-parent", accept_unknown_key=True)

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
