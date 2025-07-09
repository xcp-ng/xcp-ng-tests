from typing import TYPE_CHECKING, Literal, Optional, overload

import logging

from lib.common import _param_add, _param_clear, _param_get, _param_remove, _param_set, strtobool

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
            sr_uuid = host.pool.get_vdi_sr_uuid(uuid)
            # avoid circular import
            # FIXME should get it from Host instead
            from lib.sr import SR
            self.sr = SR(sr_uuid, host.pool)
        else:
            self.sr = sr

    def name(self) -> str:
        return self.param_get('name-label')

    def destroy(self):
        logging.info("Destroy %s", self)
        self.sr.pool.master.xe('vdi-destroy', {'uuid': self.uuid})

    def clone(self):
        uuid = self.sr.pool.master.xe('vdi-clone', {'uuid': self.uuid})
        return VDI(uuid, sr=self.sr)

    def readonly(self) -> bool:
        return strtobool(self.param_get("read-only"))

    def __str__(self):
        return f"VDI {self.uuid} on SR {self.sr.uuid}"

    @overload
    def param_get(self, param_name: str, key: Optional[str] = ...,
                  accept_unknown_key: Literal[False] = ...) -> str:
        ...

    @overload
    def param_get(self, param_name: str, key: Optional[str] = ...,
                  accept_unknown_key: Literal[True] = ...) -> Optional[str]:
        ...

    def param_get(self, param_name: str, key: Optional[str] = None,
                  accept_unknown_key: bool = False) -> Optional[str]:
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
