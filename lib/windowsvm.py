from __future__ import annotations

from lib.vm import VM

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.host import Host


class WindowsVM(VM):

    def __init__(self, uuid: str, host: 'Host'):
        super().__init__(uuid, host)
