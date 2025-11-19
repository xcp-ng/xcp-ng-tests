from __future__ import annotations

from lib.unixvm import UnixVM

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.host import Host


class DebianVM(UnixVM):

    def __init__(self, uuid: str, host: 'Host'):
        super().__init__(uuid, host)

    def configure_serial_console(self):
        self.ssh('echo GRUB_CMDLINE_LINUX_DEFAULT="console=ttyS,115200" > /etc/default/grub.d/console.cfg &&'
                 ' grub-mkconfig -o /boot/grub/grub.cfg',
                 check=False)
