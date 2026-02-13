from __future__ import annotations

import logging

from lib.common import _param_add, _param_clear, _param_get, _param_remove, _param_set
from lib.pif import PIF

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.host import Host

class VLAN:
    xe_prefix = "vlan"

    def __init__(self, host: Host, uuid: str):
        self.host = host
        self.uuid = uuid

    def param_get(self, param_name, key=None, accept_unknown_key=False):
        return _param_get(self.host, VLAN.xe_prefix, self.uuid, param_name, key, accept_unknown_key)

    def param_set(self, param_name, value, key=None):
        _param_set(self.host, VLAN.xe_prefix, self.uuid, param_name, value, key)

    def param_add(self, param_name, value, key=None):
        _param_add(self.host, VLAN.xe_prefix, self.uuid, param_name, value, key)

    def param_clear(self, param_name):
        _param_clear(self.host, VLAN.xe_prefix, self.uuid, param_name)

    def param_remove(self, param_name, key, accept_unknown_key=False):
        _param_remove(self.host, VLAN.xe_prefix, self.uuid, param_name, key, accept_unknown_key)

    def destroy(self):
        logging.info(f"Destroying VLAN: {self.uuid}")
        self.host.xe('vlan-destroy', {'uuid': self.uuid})

    def tag(self) -> int:
        tag = self.param_get('tag')
        assert tag
        return int(tag)

    def tagged_PIF(self) -> PIF:
        uuid = self.param_get("tagged-PIF")
        assert uuid
        return PIF(uuid, self.host)

    def untagged_PIF(self) -> PIF:
        uuid = self.param_get("untagged-PIF")
        assert uuid
        return PIF(uuid, self.host)
