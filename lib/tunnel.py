from __future__ import annotations

import logging

from lib.common import XeObject
from lib.pif import PIF

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.host import Host

class Tunnel(XeObject):
    xe_prefix = "tunnel"

    def __init__(self, host: Host, uuid: str):
        super().__init__(host, uuid)
        self.host = host

    def destroy(self) -> None:
        logging.info(f"Destroying Tunnel: {self.uuid}")
        self.host.xe('tunnel-destroy', {'uuid': self.uuid})

    def access_pif(self) -> PIF:
        uuid = self.param_get("access-PIF")
        assert uuid
        return PIF(uuid, self.host)

    def transport_pif(self) -> PIF:
        uuid = self.param_get("transport-PIF")
        assert uuid
        return PIF(uuid, self.host)
