from __future__ import annotations

import logging

from lib.common import XeObject
from lib.pif import PIF

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.host import Host

class VLAN(XeObject):
    xe_prefix = "vlan"

    def __init__(self, host: Host, uuid: str):
        super().__init__(host, uuid)
        self.host = host

    def destroy(self) -> None:
        logging.info(f"Destroying VLAN: {self.uuid}")
        self.host.xe('vlan-destroy', {'uuid': self.uuid})

    def tag(self) -> int:
        tag = self.param_get('tag')
        assert tag
        return int(tag)

    def tagged_pif(self) -> PIF:
        uuid = self.param_get("tagged-PIF")
        assert uuid
        return PIF(uuid, self.host)

    def untagged_pif(self) -> PIF:
        uuid = self.param_get("untagged-PIF")
        assert uuid
        return PIF(uuid, self.host)
