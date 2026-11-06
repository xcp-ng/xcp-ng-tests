from __future__ import annotations

import logging

from lib.common import XeObject, safe_split
from lib.pif import PIF

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.host import Host

class Bond(XeObject):
    xe_prefix = "bond"

    def __init__(self, host: Host, uuid: str):
        super().__init__(host, uuid)
        self.host = host

    def destroy(self) -> None:
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
