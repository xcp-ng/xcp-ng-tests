from __future__ import annotations

from lib.unixvm import UnixVM

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.host import Host


class OpenSuseVM(UnixVM):

    def __init__(self, uuid: str, host: 'Host'):
        super().__init__(uuid, host)

    def configure_serial_console(self):
        self.ssh('export F=/etc/default/grub ; '
                 '[ -f ${F} ] && '
                 ' ( grep -q " console=ttyS" ${F} || '
                 '   sed -i \'/^GRUB_CMDLINE_LINUX_DEFAULT/ { '
                 '         s/ *quiet */ /; s/ *splash=silent */ /; s/ *"$/ console=ttyS,115200"/ }\' ${F} ) && '
                 ' grub2-mkconfig -o /boot/grub2/grub.cfg',
                 check=False)
