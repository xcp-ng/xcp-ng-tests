from __future__ import annotations

from lib.unixvm import UnixVM

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.host import Host


class CentOSVM(UnixVM):

    def __init__(self, uuid: str, host: 'Host'):
        super().__init__(uuid, host)

    def configure_serial_console(self):
        self.ssh('sed -i "/^[[:space:]]*kernel / { s/ *quiet */ /; s/$/ console=ttyS,115200/ }" /boot/grub/grub.conf',
                 check=False)
