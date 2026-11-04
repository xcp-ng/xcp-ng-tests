from __future__ import annotations

import logging

from lib.common import XeObject, safe_split

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.host import Host

class Network(XeObject):
    xe_prefix = "network"

    def __init__(self, host: Host, uuid: str):
        super().__init__(host, uuid)
        self.host = host

    def destroy(self) -> None:
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
