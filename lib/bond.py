from __future__ import annotations

import logging

from lib.common import _param_add, _param_clear, _param_get, _param_remove, _param_set, safe_split
from lib.host import Host
from lib.network import Network
from lib.pif import PIF

from typing import Optional

class Bond:
    xe_prefix = "bond"

    def __init__(self, host: Host, uuid: str):
        self.host = host
        self.uuid = uuid

    def param_get(self, param_name, key=None, accept_unknown_key=False):
        return _param_get(self.host, Bond.xe_prefix, self.uuid, param_name, key, accept_unknown_key)

    def param_set(self, param_name, value, key=None):
        _param_set(self.host, Bond.xe_prefix, self.uuid, param_name, value, key)

    def param_add(self, param_name, value, key=None):
        _param_add(self.host, Bond.xe_prefix, self.uuid, param_name, value, key)

    def param_clear(self, param_name):
        _param_clear(self.host, Bond.xe_prefix, self.uuid, param_name)

    def param_remove(self, param_name, key, accept_unknown_key=False):
        _param_remove(self.host, Bond.xe_prefix, self.uuid, param_name, key, accept_unknown_key)

    @staticmethod
    def create(host: Host, network: Network, pifs: list[PIF], mode: Optional[str] = None) -> Bond:
        args: dict[str, str | bool] = {
            'network-uuid': network.uuid,
            'pif-uuids': ','.join([pif.uuid for pif in pifs]),
        }

        if mode is not None:
            args['mode'] = mode

        uuid = host.xe("bond-create", args, minimal=True)
        logging.info(f"New Bond: {uuid}")

        return Bond(host, uuid)

    def destroy(self):
        logging.info(f"Destroying bond: {self.uuid}")
        self.host.xe('bond-destroy', {'uuid': self.uuid})

    def master(self) -> PIF:
        uuid = self.param_get('master')
        assert uuid is not None, "no master on Bond"
        return PIF(uuid, self.host)

    def slaves(self) -> list[str]:
        return safe_split(self.param_get('slaves'))

    def mode(self) -> Optional[str]:
        return self.param_get("mode")
