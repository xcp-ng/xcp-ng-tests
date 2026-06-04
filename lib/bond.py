from __future__ import annotations

import logging

from lib.common import _param_add, _param_clear, _param_get, _param_remove, _param_set, safe_split
from lib.pif import PIF

from typing import TYPE_CHECKING, Literal, overload

if TYPE_CHECKING:
    from lib.host import Host

class Bond:
    xe_prefix = "bond"

    def __init__(self, host: Host, uuid: str):
        self.host = host
        self.uuid = uuid

    @overload
    def param_get(self, param_name: str, key: str | None = ...,
                  accept_unknown_key: Literal[False] = ...) -> str:
        ...

    @overload
    def param_get(self, param_name: str, key: str | None = ...,
                  accept_unknown_key: Literal[True] = ...) -> str | None:
        ...

    def param_get(self, param_name: str, key: str | None = None, accept_unknown_key: bool = False) -> str | None:
        return _param_get(self.host, self.xe_prefix, self.uuid,
                          param_name, key, accept_unknown_key)

    def param_set(self, param_name: str, value: str, key: str | None = None) -> None:
        _param_set(self.host, self.xe_prefix, self.uuid,
                   param_name, value, key)

    def param_remove(self, param_name: str, key: str, accept_unknown_key: bool = False) -> None:
        _param_remove(self.host, self.xe_prefix, self.uuid,
                      param_name, key, accept_unknown_key)

    def param_add(self, param_name: str, value: str, key: str | None = None) -> None:
        _param_add(self.host, self.xe_prefix, self.uuid,
                   param_name, value, key)

    def param_clear(self, param_name: str) -> None:
        _param_clear(self.host, self.xe_prefix, self.uuid,
                     param_name)

    def destroy(self):
        logging.info(f"Destroying bond: {self.uuid}")
        self.host.xe('bond-destroy', {'uuid': self.uuid})

    def master(self) -> PIF:
        uuid = self.param_get('master')
        assert uuid is not None, "no master on Bond"
        return PIF(uuid, self.host)

    def slaves(self) -> list[str]:
        return safe_split(self.param_get('slaves'), sep='; ')

    def mode(self) -> str | None:
        return self.param_get("mode")
