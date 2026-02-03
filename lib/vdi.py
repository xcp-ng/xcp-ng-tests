from __future__ import annotations

import logging

from lib.common import (
    _param_add,
    _param_clear,
    _param_get,
    _param_remove,
    _param_set,
    ensure_type,
    strtobool,
    wait_for_not,
)

from typing import TYPE_CHECKING, Callable, Literal, Optional, TypeVar, overload

if TYPE_CHECKING:
    from lib.host import Host
    from lib.sr import SR

R = TypeVar("R")

ImageFormat = Literal['qcow2', 'raw', 'vhd']

class VDI:
    xe_prefix = "vdi"
    sr: SR

    @overload
    def __init__(self, uuid: str, *, host: Host, sr: Literal[None] = None) -> None:
        ...

    @overload
    def __init__(self, uuid: str, *, host: Literal[None] = None, sr: SR) -> None:
        ...

    def __init__(self, uuid: str, *, host: Host | None = None, sr: SR | None = None) -> None:
        self.uuid = uuid
        # TODO: use a different approach when migration is possible
        if sr is None:
            assert host
            sr = host.get_sr_from_vdi_uuid(self.uuid)
            assert sr is not None
            self.sr = sr
        else:
            self.sr = sr

    def name(self) -> str:
        return self.param_get('name-label')

    def destroy(self) -> None:
        logging.info("Destroy %s", self)
        self.sr.pool.master.xe('vdi-destroy', {'uuid': self.uuid})

    def clone(self) -> VDI:
        uuid = self.sr.pool.master.xe('vdi-clone', {'uuid': self.uuid})
        return VDI(uuid, sr=self.sr)

    def snapshot(self) -> VDI:
        uuid = self.sr.pool.master.xe('vdi-snapshot', {'uuid': self.uuid})
        return VDI(uuid, sr=self.sr)

    def readonly(self) -> bool:
        return strtobool(self.param_get("read-only"))

    def get_virtual_size(self) -> int:
        return int(self.param_get("virtual-size"))

    def resize(self, new_size: int) -> None:
        logging.info(f"Resizing VDI {self.uuid} to {new_size}")
        self.sr.pool.master.xe("vdi-resize", {"uuid": self.uuid, "disk-size": str(new_size)})

    def __str__(self) -> str:
        return f"VDI {self.uuid} on SR {self.sr.uuid}"

    def get_parent(self) -> Optional[str]:
        return self.param_get("sm-config", key="vhd-parent", accept_unknown_key=True)

    def get_image_format(self) -> ImageFormat | None:
        v = self.param_get("sm-config", key="image-format", accept_unknown_key=True)
        if v is None:
            return None
        return ensure_type(ImageFormat, v)

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

    def param_set(self, param_name: str, value: str, key: str | None = None) -> None:
        _param_set(self.sr.pool.master, self.xe_prefix, self.uuid,
                   param_name, value, key)

    def param_add(self, param_name: str, value: str, key: str | None = None) -> None:
        _param_add(self.sr.pool.master, self.xe_prefix, self.uuid,
                   param_name, value, key)

    def param_clear(self, param_name: str) -> None:
        _param_clear(self.sr.pool.master, self.xe_prefix, self.uuid,
                     param_name)

    def param_remove(self, param_name: str, key: str, accept_unknown_key: bool = False) -> None:
        _param_remove(self.sr.pool.master, self.xe_prefix, self.uuid,
                      param_name, key, accept_unknown_key)

    def wait_for_coalesce(self, fn: Callable[[], R] | None = None) -> R | None:
        previous_parent = self.get_parent()
        ret = None
        if fn is not None:
            ret = fn()
        # It is necessary to wait a long time because the GC can be paused for more than 5 minutes.
        # And it is also necessary to allow a sufficiently long merge time which depends on the amount of data.
        wait_for_not(lambda: self.get_parent() != previous_parent, msg="Waiting for coalesce", timeout_secs=10 * 60)
        logging.info("Coalesce done")
        return ret
