from __future__ import annotations

import logging

from lib.common import _param_add, _param_clear, _param_get, _param_remove, _param_set, safe_split
from lib.host import Host

from typing import Optional

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

    @staticmethod
    def create(host: Host, label: str, description: Optional[str] = None) -> Network:
        args: dict[str, str | bool] = {
            'name-label': label,
        }

        if description is not None:
            args['name-description'] = description

        logging.info(f"Creating network '{label}'")
        uuid = host.xe("network-create", args, minimal=True)
        logging.info(f"New Network: {uuid}")

        return Network(host, uuid)

    def destroy(self):
        logging.info(f"Destroying network '{self.param_get('name-label')}': {self.uuid}")
        self.host.xe('network-destroy', {'uuid': self.uuid})

    def PIF_uuids(self) -> list[str]:
        return safe_split(self.param_get('PIF-uuids'), '; ')

    def VIF_uuids(self) -> list[str]:
        return safe_split(self.param_get('VIF-uuids'), '; ')

    def is_private(self) -> bool:
        return len(self.PIF_uuids()) == 0

    def managed(self) -> bool:
        return self.param_get('managed') == 'true'

    def MTU(self) -> int:
        return int(self.param_get('MTU') or '0')
