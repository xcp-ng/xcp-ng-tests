from __future__ import annotations

import logging

from lib.common import _param_add, _param_clear, _param_get, _param_remove, _param_set, safe_split

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.host import Host

class Network:
    xe_prefix = "network"

    def __init__(self, host: Host, uuid: str):
        self.host = host
        self.uuid = uuid

    def param_get(self, param_name, key=None, accept_unknown_key=False):
        return _param_get(self.host, Network.xe_prefix, self.uuid, param_name, key, accept_unknown_key)

    def param_set(self, param_name, value, key=None):
        _param_set(self.host, Network.xe_prefix, self.uuid, param_name, value, key)

    def param_add(self, param_name, value, key=None):
        _param_add(self.host, Network.xe_prefix, self.uuid, param_name, value, key)

    def param_clear(self, param_name):
        _param_clear(self.host, Network.xe_prefix, self.uuid, param_name)

    def param_remove(self, param_name, key, accept_unknown_key=False):
        _param_remove(self.host, Network.xe_prefix, self.uuid, param_name, key, accept_unknown_key)

    def destroy(self):
        logging.info(f"Destroying network '{self.param_get('name-label')}': {self.uuid}")
        self.host.xe('network-destroy', {'uuid': self.uuid})

    def pif_uuids(self) -> list[str]:
        return safe_split(self.param_get('PIF-uuids'), '; ')

    def vif_uuids(self) -> list[str]:
        return safe_split(self.param_get('VIF-uuids'), '; ')

    def is_private(self) -> bool:
        return len(self.pif_uuids()) == 0

    def managed(self) -> bool:
        return self.param_get('managed') == 'true'

    def MTU(self) -> int:
        return int(self.param_get('MTU') or '0')

    def bridge(self) -> str:
        bridge = self.param_get('bridge')
        assert bridge is not None, "network must have a bridge"
        return bridge
